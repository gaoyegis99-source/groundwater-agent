# o200k_base 构建输入

文件来源：<https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken>。

固定 SHA-256：`446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d`，与锁定 tiktoken 0.13.0 的 `tiktoken_ext.openai_public.o200k_base` 校验值相同。大小为 3,613,922 字节。

本文件仅包含分词器词表数据。纳入时通过当前环境代理从上述来源下载，并独立核对官方校验值及此前成功构建的 Kernel 缓存，字节完全一致。Docker 构建断网验证文件及编码器，运行用户再次断网验证，避免每次客户端或 Kernel 源码修改都重新下载这个不变依赖。缓存文件名由来源 URL 的 SHA-1 决定；内容完整性使用上述 SHA-256。
