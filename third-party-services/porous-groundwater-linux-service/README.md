# 孔隙型地下水 Linux 服务

本机迁移从原始 v2.6.2 ZIP 构建原生 Linux 求解器。构建验证完整交付包和四个嵌套源码包的 SHA256。编译产物与随包源码保存在仓库外的私有目录。

交付包将 PEST 源码标为 18.8，内层源码的 `version.inc` 和 Linux 二进制版本均为 18.7。
构建清单分别记录这两个版本。MODFLOW-NWT 构建清单记录了未被主程序引用、缺少头文件的旧重启单元。

在项目根目录构建工具链，随后在禁网容器中编译求解器。

```bash
recipes="$PWD/third-party-services/porous-groundwater-linux-service"
build_dir="$(mktemp -d "${XDG_CACHE_HOME:-$HOME/.cache}/groundwater-v6/porous-build.XXXXXXXX")"
bin/docker build -t groundwater-v6/porous-toolchain:bookworm -f "$recipes/Dockerfile.toolchain" "$recipes"
bin/docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
  -v "$delivery_zip:/delivery.zip:ro" -v "$recipes/scripts:/recipes:ro" \
  -v "$build_dir:/native-build" groundwater-v6/porous-toolchain:bookworm \
  sh /recipes/build-solvers.sh /delivery.zip /native-build
```

构建完成后，使用同一工具链容器运行
`python /recipes/smoke_native_cases.py /delivery.zip /native-build /scratch/cases`，
并将私有可写目录挂载到 `/scratch`。该检查使用原版插件的计算入口以及包内固定案例，
保留求解器输出、控制台日志和摘要，以便进一步核对 Windows 数值基准。

服务 1.1.0 另外构建保留完整弥散张量的 `mt3dusgs-afc`，原版四个二进制仍保留。
将整个服务目录挂载到 `/service:ro`，将一个新的私有目录挂载到 `/afc-build`，在工具链中执行：

```bash
python /service/scripts/build_afc_solver.py /delivery.zip /afc-build/native --compiler gfortran
```

`native/afc-manifest.json` 记录 MT3D 源码包、逐文件补丁、补丁后源码树、编译器、构建脚本和
最终二进制摘要；装配的 `solver-manifest.json` 另记录外层原始 ZIP 摘要。
下方 `$afc_build_dir` 指向宿主机对应的 `native` 目录。

## 构建运行镜像

`stage_service.py` 读取原交付 ZIP、求解器构建目录及 V6 缓存的 Linux x86-64 科学计算轮包。
它核验源码、求解器与轮包摘要并在仓库外生成私有镜像构建上下文及矩形场地输入。
传入与二进制兼容的 `libquadmath.so.0`、`libexpat.so.1` 路径。动态链接 Fortran 运行库的构建还需传入
`--libgfortran`。镜像内的清单记录实际使用的库和原版 worker 摘要。

```bash
source/.venv/bin/python -B "$recipes/scripts/stage_service.py" "$delivery_zip" "$build_dir" "$staged_dir" \
  --afc-build "$afc_build_dir" \
  --native-wheelhouse "$PLATFORM_DEPENDENCY_ROOT/python/native" \
  --libquadmath "$(gcc -print-file-name=libquadmath.so.0)" \
  --libexpat /usr/lib/x86_64-linux-gnu/libexpat.so.1
bin/docker build -t groundwater-v6/porous-groundwater:1.1.1 "$staged_dir/image"
```

## 操作输入和成果

| 操作 | 数据约定 |
| --- | --- |
| 编译、仿真、分析与出图 | 上传只含 `model_definition.json` 的项目 ZIP。平台在节点间传递带校验的编译包和结果包，发布水头、浓度、PNG、VTU/PVD 与运行凭据。 |
| PEST 水头率定 | 观测 JSON 的 `schemaVersion` 为 `porous-pest-head.v1`。使用 `parameters` 声明全局倍率及上下界，使用 `observations` 声明时间、层行列、观测水头和权重。新算子版本发布可重算模型包。 |
| 情景对比 | ZIP 包含 `model_definition.json` 和 `scenarios.json`，后者为 `porous-scenarios.v1`。在固定基准上应用独立的全局倍率，发布逐情景结果及对比表。 |
| 地图准备 | 接收本地有坐标参考系的 GeoTIFF。发布保留栅格值的 GeoTIFF 和坐标系、范围、无数据值及波段元数据。 |
| 训练与采样 | 训练 ZIP 包含 `training.json` 和三个 NPY 文件 `perm.npy`、`disp.npy`、`head.npy`。同一批次样本形状分别为 `(N,5,64,64)`、`(N,65,65)`、`(N,50,2)`。采样读取训练权重及 `porous-inversion-sampling.v1` JSON。后验数组使用 `log10(K)`，实际设备写入指标。 |
| 农业抽水反演 | ZIP 包含 `model_definition.json` 与 `agricultural.json`。显式声明农业抽水 WEL 记录、监测位置、观测时段及率定分割点。以两级距离环分组，计算 MODFLOW 响应、拟合有界岭回归，并发布独立验证和重算结果。 |

本机发布使用 CPU 镜像。部署可通过 `--device cuda` 选择带 CUDA 支持的服务镜像。
现场科学等价待验证。

产品启用以[第三方模型接入要求](../../source/docs/operations/deployment/third-party-groundwater-models.md)中的验收状态为准。

## V6 接口封装

服务 1.1.1 使用 `remote-operation.v1`，数值模型仍为 v2。匿名 `/healthz`、`/readyz`、
`/version` 与受保护的 capabilities、分片上传、提交、幂等查询、状态、取消、结果及 receipt
端点组成 Worker 接口；用户通过 V6 Public API v12 和 Workflow v6 使用它。
所有错误带 `X-Request-ID`；字段校验使用 `REQUEST_VALIDATION_ERROR` 及字段位置/类型，
不回显输入值。401 带 Bearer challenge。无效请求不会创建任务。

请求 Schema 位于 `contracts/remote-operation.v1.json`，从真实接口模型生成，包含十项操作
的精确端口、格式、大小上限、校验和及参数约束。修改接口后执行
`python scripts/export_request_contract.py`，检查时使用 `--check`。
`tests/test_remote_contract.py` 核对 V6 算子声明、认证、字段错误、幂等重放/冲突、取消和成果身份；
队列调度在单元测试中暂停，真实算法及成果发布使用隔离 V6 工作流验收。
测试环境需 pytest、httpx 和 jsonschema；从服务目录执行 `python -m pytest -q tests`。


### 浓度数值质量与守恒修正（2026-11-11）

仿真及情景对比的 `transport.quality` 检查全部保存时刻、各层有效网格，报告最小值、
负值网格记录数、最差网格位置和 `review_required`。分析节点的元数据继续传递该诊断。
原始 UCN 不裁剪、不改写；求解器正常终止与质量平衡通过并不代表浓度非负。

随包矩形场地的负值经控制实验定位为交叉弥散离散下冲：提高 GCG 精度和采用完整隐式
交叉项仍有负值；仅省略交叉项后，三种抽水情景的全部八个保存时刻均无负值。
`transport.mt3d.dsp.noCross` 为显式布尔诊断开关，默认 `false`。设为 `true` 写入
MT3D-USGS 的 `$ NOCROSS`；结果始终带 `CROSS_DISPERSION_DISABLED` 复核警告。
该设置改变弥散方程，不能声称与原模型科学等价，也不能替代网格收敛或现场验证。
[USGS MT3D-USGS 手册，Dispersion Package](https://pubs.usgs.gov/tm/06/a53/tm06a53.pdf)
NOCROSS 仅能在显式 `transport.mt3d.numerics="legacy"` 下使用。

构建时 `scripts/patch_transport_worker.py` 对原包 worker 作可核对的最小输入补丁；
`solver-manifest.json.servicePatches` 保存补丁、修改前后 worker 的 SHA-256。
服务 1.1.0 / 模型 `porous-groundwater-v2.6.2-linux.v2` 默认使用
`transport.mt3d.numerics="conservative-afc.v1"`。这是本地实现的隐式代数通量修正，
不是对原版结果的裁剪，也不声称复现任何论文的完整 FCT 算法。

每个运移步保留完整交叉弥散矩阵 `H`，构造对称图黏性 `D`，使 `L=H+D`
具有非正非对角元和严格正的行和。以原 GCG 解形成反扩散通量，按供体右端质量预算限流；
同一通量在相邻单元一加一减，再用双精度求解 `L c=b+f`。固定浓度边界额外交换的质量
计入常浓度收支。约 `1e-17` 量级的显式 TVD 右端舍入负值，仅在同一活动连通域中
找供体等量转移；超阈值或无供体即失败，转移量单独记录。求解后不裁剪浓度。

当前适用范围是单组分、无反应、TVD 对流、具有正储存项的隐式运移。NOCROSS、其他对流
方法、反应或未验证的耦合包会明确拒绝；不会静默降级。显式 `numerics="legacy"`
使用原始求解器，保留原有质量诊断。新方法强制完整隐式交叉项和至少十次 GCG 外迭代，
预测解不收敛立即失败。供体预算限流较保守，需评估新增数值扩散。

`transport.numerics` 报告逐步最小浓度、成对通量质量残差、线性求解残差、边界质量修正
及舍入转移量；完整记录在结果 ZIP 的 `raw/transport.afc.csv`。审计缺失、负浓度、
残差超限或末时刻不符会阻止发布。`effective_parameters.json` 记录实际方法和求解参数。

独立验证命令 `scripts/verify_afc.py --help` 使用生产 Fortran 内核，覆盖封闭域变体积脉冲、
常浓度、固定浓度边界质量收支以及旋转各向异性高斯解析解。验证环境需 NumPy/SciPy。
在私有工作目录编译测试适配器，然后运行：

```bash
gfortran -O2 -frecursive -ffree-line-length-none "$recipes/tests/afc_driver.f90" \
  "$recipes/native/afc.f90" -o afc-driver
python "$recipes/scripts/verify_afc.py" --driver "$PWD/afc-driver" --output "$PWD/verification"
```

2026-11-11 本机脉冲相对质量误差 `1.21e-7`；
高斯网格 41/81/161 的 L1 误差为 0.0770/0.0106/0.00332，交叉矩接近解析值 0.448。
同一矩形场地三情景共 720 步均无负浓度，最大质量平衡误差 0.000388%，水头二进制不变。
原包 M1、M2 浓度仍满足 `rtol=1e-5, atol=1e-7`；W1 最大变化 0.000516 mg/L，
不满足原容差，保留失败记录，不能以新基准替代旧解科学等价证明。现场科学等价待验证。
