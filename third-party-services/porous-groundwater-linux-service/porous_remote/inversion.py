"""Headless training and posterior sampling with the bundled conditioned UNet."""

from io import BytesIO
import json
import math
import os
from pathlib import Path
from zipfile import ZipFile

from .packages import MAX_BYTES, MAX_DEFINITION, seal, write_json

METHOD = "conditional-ddpm-aquifer-field-v1"
SHAPES = {"perm": (5, 64, 64), "disp": (65, 65), "head": (50, 2)}
MODEL_KEYS = {"baseChannels", "conditionDimension", "timeEmbeddingDimension", "diffusionSteps"}


def _integer(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("INVERSION_INTEGER_INVALID")
    return value


def _number(value: object, minimum: float, maximum: float) -> float:
    if type(value) not in (float, int) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError("INVERSION_NUMBER_INVALID")
    return float(value)


def _model_config(config: dict) -> dict:
    if not isinstance(config, dict) or set(config) != MODEL_KEYS:
        raise ValueError("INVERSION_MODEL_CONFIG_INVALID")
    if config["baseChannels"] not in (32, 64, 96):
        raise ValueError("INVERSION_MODEL_CONFIG_INVALID")
    _integer(config["conditionDimension"], 16, 192)
    if _integer(config["timeEmbeddingDimension"], 16, 256) % 2:
        raise ValueError("INVERSION_MODEL_CONFIG_INVALID")
    _integer(config["diffusionSteps"], 4, 1000)
    return config


def _runtime(seed: int):
    import torch

    selected = os.environ.get("POROUS_DEVICE", "cpu")
    if selected not in {"cpu", "cuda"} or selected == "cuda" and not torch.cuda.is_available():
        raise ValueError("INVERSION_DEVICE_UNAVAILABLE")
    torch.set_num_threads(2)
    torch.use_deterministic_algorithms(True)
    torch.manual_seed(seed)
    return torch, torch.device("cpu" if selected == "cpu" else "cuda:0")


def _device_name(torch, device) -> str:
    return str(device) if device.type == "cpu" else f"{device}: {torch.cuda.get_device_name(device)}"


def _model(config, device):
    from gw_platform.conditional_diffusion.unet_hybrid import UNetConditioned

    return UNetConditioned(
        base_ch=config["baseChannels"], time_dim=config["timeEmbeddingDimension"],
        cond_dim=config["conditionDimension"], time_steps=2, num_wells=50, data_ch=5,
    ).to(device)


def _preprocess(name, values):
    import numpy as np

    array = np.asarray(values, dtype=np.float32)
    if array.shape[-len(SHAPES[name]):] != SHAPES[name] or not np.isfinite(array).all():
        raise ValueError("INVERSION_ARRAY_INVALID")
    if name == "perm":
        return np.log10(np.maximum(array, 1e-20))
    if name == "disp":
        return np.log10(np.maximum(-array[..., :64, :64], 1e-4))[..., None, :, :]
    return np.log10(np.maximum(-array, 5e-4))


def _read_npy(archive: ZipFile, name: str, dimensions: tuple):
    import numpy as np
    from numpy.lib import format as npy

    member = archive.getinfo(name + ".npy")
    if member.file_size > 32 * 1024**2:
        raise ValueError("INVERSION_ARRAY_LIMIT_EXCEEDED")
    raw = archive.read(member)
    stream = BytesIO(raw)
    version = npy.read_magic(stream)
    if version != (1, 0):
        raise ValueError("INVERSION_ARRAY_VERSION_INVALID")
    shape, _, dtype = npy.read_array_header_1_0(stream)
    if (
        len(shape) != len(dimensions) + 1 or shape[1:] != dimensions or not 3 <= shape[0] <= 256
        or dtype.kind != "f" or dtype.itemsize not in (4, 8)
        or math.prod(shape) * dtype.itemsize != len(raw) - stream.tell()
    ):
        raise ValueError("INVERSION_ARRAY_INVALID")
    return _preprocess(name, np.load(BytesIO(raw), allow_pickle=False))


def _training_data(source: Path) -> tuple[dict, dict]:
    with ZipFile(source) as archive:
        names = {"training.json", *(name + ".npy" for name in SHAPES)}
        if len(archive.infolist()) != len(names) or set(archive.namelist()) != names:
            raise ValueError("INVERSION_TRAINING_PACKAGE_INVALID")
        if archive.getinfo("training.json").file_size > MAX_DEFINITION:
            raise ValueError("INVERSION_TRAINING_PACKAGE_INVALID")
        config = json.loads(archive.read("training.json"))
        arrays = {name: _read_npy(archive, name, shape) for name, shape in SHAPES.items()}
    if (
        not isinstance(config, dict)
        or set(config) != {"schemaVersion", "model", "seed", "epochs", "batchSize", "learningRate"}
        or config["schemaVersion"] != "porous-inversion-training.v1"
        or len({len(array) for array in arrays.values()}) != 1
    ):
        raise ValueError("INVERSION_TRAINING_CONFIG_INVALID")
    _model_config(config["model"])
    _integer(config["seed"], 0, 2**31 - 1)
    _integer(config["epochs"], 1, 1000)
    _integer(config["batchSize"], 1, 4)
    _number(config["learningRate"], 1e-6, 0.01)
    return config, arrays


def _normalize(array, scale):
    return (2 * (array - scale["minimum"]) / scale["span"] - 1).astype("float32")


def _schedule(torch, config, device):
    steps = config["diffusionSteps"]
    beta = torch.linspace(0.0001**0.5, 0.02**0.5, steps, device=device).square()
    return (1 - beta).cumprod(0)


def _report(job: Path, role: str, metrics: dict, key: bytes, files=None) -> dict:
    metadata = job / "metrics.json"
    write_json(metadata, metrics)
    seal(job / "report_package.zip", role, {metadata.name: metadata, **(files or {})}, key)
    return {"metrics": metrics}


def train(source: Path, job: Path, key: bytes) -> dict:
    import numpy as np

    config, arrays = _training_data(source)
    torch, device = _runtime(config["seed"])
    model = _model(config["model"], device)
    schedule = _schedule(torch, config["model"], device)
    optimizer = torch.optim.Adam(model.parameters(), lr=config["learningRate"])
    rng = np.random.default_rng(config["seed"])
    order = rng.permutation(len(arrays["perm"]))
    validation_count = max(1, len(order) // 10)
    validation, training = order[:validation_count], order[validation_count:]
    scales = {}
    inputs = {}
    for name, array in arrays.items():
        minimum = float(array[training].min())
        span = float(array[training].max()) - minimum
        scales[name] = {"minimum": minimum, "span": span if span > 1e-12 else 1.0}
        inputs[name] = torch.from_numpy(_normalize(array, scales[name])).to(device)
    device_name = _device_name(torch, device)
    scale_file = job / "scalers.json"
    write_json(scale_file, scales)

    def epoch(indices, *, update):
        losses = []
        model.train(update)
        batch_size = config["batchSize"]
        with torch.set_grad_enabled(update):
            for offset in range(0, len(indices), batch_size):
                batch = indices[offset:offset + batch_size].tolist()
                perm, disp, head = (inputs[name][batch] for name in SHAPES)
                noise = torch.randn_like(perm)
                timestep = torch.randint(len(schedule), (len(batch),), device=device)
                alpha = schedule[timestep].reshape(-1, 1, 1, 1)
                if update:
                    drop = torch.rand(len(batch), device=device) < 0.25
                    disp[drop] = 0
                    head[drop] = 0
                prediction = model(alpha.sqrt() * perm + (1 - alpha).sqrt() * noise, timestep, disp, head)
                loss = torch.nn.functional.mse_loss(prediction, noise)
                if update:
                    optimizer.zero_grad(set_to_none=True)
                    loss.backward()
                    optimizer.step()
                losses.append(float(loss.detach()) * len(batch))
        return math.fsum(losses) / len(indices)

    best = math.inf
    curve = []
    for _ in range(config["epochs"]):
        train_loss = epoch(rng.permutation(training), update=True)
        validation_loss = epoch(validation, update=False)
        if not all(math.isfinite(item) for item in (train_loss, validation_loss)):
            raise ValueError("INVERSION_NONFINITE_LOSS")
        curve.append({"training": train_loss, "validation": validation_loss})
        if validation_loss < best:
            best = validation_loss
            torch.save({
                "method": METHOD, "modelConfig": config["model"],
                "scalersJson": scale_file.read_text(),
                "model": {name: tensor.detach().cpu() for name, tensor in model.state_dict().items()},
            }, job / "weights.binary")
    metrics = {
        "method": METHOD, "device": device_name, "seed": config["seed"],
        "trainingCount": len(training), "validationCount": len(validation),
        "bestValidationLoss": best, "epochs": curve,
    }
    return _report(job, "inversion-training", metrics, key, {scale_file.name: scale_file})


def _sample_config(source: Path) -> dict:
    if source.stat().st_size > MAX_DEFINITION:
        raise ValueError("INVERSION_SAMPLE_CONFIG_INVALID")
    config = json.loads(source.read_bytes())
    required = {"schemaVersion", "seed", "samples", "steps", "guidance", "disp", "head"}
    if (
        not isinstance(config, dict) or set(config) not in (required, required | {"referencePerm"})
        or config["schemaVersion"] != "porous-inversion-sampling.v1"
    ):
        raise ValueError("INVERSION_SAMPLE_CONFIG_INVALID")
    _integer(config["seed"], 0, 2**31 - 1)
    _integer(config["samples"], 1, 64)
    _number(config["guidance"], 0, 20)
    return config


def _reverse_step(torch, alpha, previous_alpha, sample, noise, generator, timestep):
    # DDPM epsilon prediction with fixed-small posterior variance and no clipping.
    # https://huggingface.co/docs/diffusers/en/api/schedulers/ddpm
    variance = 1 - alpha
    prior_variance = 1 - previous_alpha
    step_alpha = alpha / previous_alpha
    x0 = (sample - variance.sqrt() * noise) / alpha.sqrt()
    estimate = (previous_alpha.sqrt() * (1 - step_alpha) * x0
                + step_alpha.sqrt() * prior_variance * sample) / variance
    if timestep == 0:
        return estimate
    posterior_variance = (prior_variance * (1 - step_alpha) / variance).clamp_min(1e-20)
    return estimate + posterior_variance.sqrt() * torch.randn(
        sample.shape, generator=generator, device=sample.device,
    )


def sample(weights: Path, source: Path, job: Path, key: bytes) -> dict:
    import numpy as np

    config = _sample_config(source)
    torch, device = _runtime(config["seed"])
    if weights.stat().st_size > MAX_BYTES:
        raise ValueError("INVERSION_WEIGHTS_LIMIT_EXCEEDED")
    checkpoint = torch.load(weights, map_location="cpu", weights_only=True)
    if (
        not isinstance(checkpoint, dict) or set(checkpoint) != {"method", "modelConfig", "scalersJson", "model"}
        or checkpoint["method"] != METHOD
    ):
        raise ValueError("INVERSION_WEIGHTS_INVALID")
    model_config = _model_config(checkpoint["modelConfig"])
    scales = json.loads(checkpoint["scalersJson"])
    if not isinstance(scales, dict) or set(scales) != set(SHAPES):
        raise ValueError("INVERSION_SCALER_INVALID")
    for scale in scales.values():
        if not isinstance(scale, dict) or set(scale) != {"minimum", "span"}:
            raise ValueError("INVERSION_SCALER_INVALID")
        _number(scale["minimum"], -100, 100)
        _number(scale["span"], 1e-12, 200)
    model = _model(model_config, device)
    state = checkpoint["model"]
    expected = model.state_dict()
    if not isinstance(state, dict) or set(state) != set(expected) or any(
        not isinstance(tensor, torch.Tensor) or tensor.layout != torch.strided
        or tensor.shape != expected[name].shape or tensor.dtype != expected[name].dtype
        or not torch.isfinite(tensor).all()
        for name, tensor in state.items()
    ):
        raise ValueError("INVERSION_WEIGHTS_INVALID")
    model.load_state_dict(state)
    model.eval()
    disp, head = (torch.from_numpy(_normalize(_preprocess(name, config[name]), scales[name]))
                  .unsqueeze(0).to(device) for name in ("disp", "head"))
    steps = _integer(config["steps"], 2, model_config["diffusionSteps"])
    schedule = _schedule(torch, model_config, device)
    timesteps = list(reversed(range(0, (len(schedule) // steps) * steps, len(schedule) // steps)))
    pairs = zip(timesteps, [*timesteps[1:], -1], strict=True)
    schedule_pairs = list(pairs)
    samples = []
    scale = scales["perm"]
    with torch.inference_mode():
        for index in range(config["samples"]):
            generator = torch.Generator(device=device).manual_seed(config["seed"] + index)
            latent = torch.randn((1, *SHAPES["perm"]), generator=generator, device=device)
            for timestep, previous in schedule_pairs:
                t = torch.tensor([timestep], device=device)
                conditioned = model(latent, t, disp, head)
                unconditioned = model(latent, t, torch.zeros_like(disp), torch.zeros_like(head))
                predicted = unconditioned + config["guidance"] * (conditioned - unconditioned)
                alpha_previous = schedule[previous] if previous >= 0 else torch.ones((), device=device)
                latent = _reverse_step(torch, schedule[timestep], alpha_previous, latent, predicted, generator, timestep)
            samples.append((latent[0].cpu().numpy() + 1) * (scale["span"] / 2) + scale["minimum"])
    posterior = np.stack(samples)
    if not np.isfinite(posterior).all():
        raise ValueError("INVERSION_NONFINITE_POSTERIOR")
    np.save(job / "posterior_samples.npy", posterior, allow_pickle=False)
    mean = posterior.mean(0)
    std = posterior.std(0)
    np.save(job / "posterior_mean_log.npy", mean, allow_pickle=False)
    np.save(job / "posterior_std_log.npy", std, allow_pickle=False)
    metrics = {
        "method": METHOD, "device": _device_name(torch, device), "seed": config["seed"], "shape": list(posterior.shape),
        "quantity": "log10(K)", "meanPosteriorStd": float(std.mean()), "steps": steps,
    }
    if "referencePerm" in config:
        reference = _preprocess("perm", config["referencePerm"])
        error = mean - reference
        metrics.update({"rmse": float(np.sqrt(np.mean(error**2))), "mae": float(np.mean(np.abs(error)))})
    return _report(job, "inversion-sampling", metrics, key, {
        name: job / name for name in ("posterior_mean_log.npy", "posterior_std_log.npy")
    })
