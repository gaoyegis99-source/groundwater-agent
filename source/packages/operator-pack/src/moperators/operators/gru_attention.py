"""CPU GRU/attention operators. Importing the catalog does not import Torch."""

from __future__ import annotations

import io
import pickle
import random

import numpy as np

from moperators.operators.transfer_common import (
    array,
    artifact,
    one,
    read_json,
    table,
    write_json,
    write_table,
)


def _seed(seed):
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)


def _model(spec):
    from torch import nn

    if spec["hiddenSize"] % spec["numHeads"]:
        raise ValueError("ATTENTION_DIMENSIONS_INVALID")

    class GruAttention(nn.Module):
        def __init__(self):
            super().__init__()
            self.gru = nn.GRU(
                spec["inputFeatures"],
                spec["hiddenSize"],
                spec["numLayers"],
                batch_first=True,
                dropout=spec["dropout"] if spec["numLayers"] > 1 else 0.0,
            )
            self.attention = nn.MultiheadAttention(
                spec["hiddenSize"], spec["numHeads"], dropout=spec["dropout"], batch_first=True
            )
            self.norm = nn.LayerNorm(spec["hiddenSize"])
            self.dropout = nn.Dropout(spec["dropout"])
            self.output = nn.Linear(spec["hiddenSize"], 1)

        def forward(self, values):
            gru_values, _ = self.gru(values)
            attended, _ = self.attention(gru_values, gru_values, gru_values, need_weights=False)
            final = self.norm(gru_values + attended)[:, -1, :]
            return self.output(self.dropout(final))

    return GruAttention()


def inspect_weights(path):
    import torch

    try:
        weights = torch.load(path, map_location="cpu", weights_only=True)
    except (pickle.UnpicklingError, RuntimeError, EOFError) as error:
        raise ValueError("MODEL_WEIGHTS_INVALID") from error
    if not isinstance(weights, dict) or not weights:
        raise ValueError("MODEL_WEIGHTS_INVALID")
    if any(
        not isinstance(key, str)
        or not isinstance(value, torch.Tensor)
        or value.dtype != torch.float32
        or not torch.isfinite(value).all()
        for key, value in weights.items()
    ):
        raise ValueError("MODEL_WEIGHTS_INVALID")
    return weights


def _restore(inputs, features):
    spec = read_json(one(inputs, "modelSpec"), "gru-attention-model.v1")
    if features.shape[1:] != (spec["inputWindow"], spec["inputFeatures"]):
        raise ValueError("TENSOR_SHAPE_INVALID")
    model = _model(spec)
    try:
        model.load_state_dict(inspect_weights(one(inputs, "weights")), strict=True)
    except RuntimeError as error:
        raise ValueError("MODEL_WEIGHTS_INVALID") from error
    return model, spec


def _train(model, features, targets, parameters):
    import torch
    from torch.utils.data import DataLoader, TensorDataset

    if targets.shape != (len(features), 1):
        raise ValueError("TENSOR_SHAPE_INVALID")
    loader = DataLoader(
        TensorDataset(torch.from_numpy(features), torch.from_numpy(targets)),
        batch_size=parameters["batchSize"],
        shuffle=True,
        generator=torch.Generator().manual_seed(parameters["seed"]),
    )
    optimizer = torch.optim.Adam(model.parameters(), lr=parameters["learningRate"])
    loss_function = torch.nn.MSELoss()
    losses = []
    for epoch in range(parameters["epochs"]):
        model.train()
        total, samples = 0.0, 0
        for values, labels in loader:
            optimizer.zero_grad(set_to_none=True)
            loss = loss_function(model(values), labels)
            if not torch.isfinite(loss):
                raise ValueError("TRAINING_NON_FINITE")
            loss.backward()
            optimizer.step()
            total += float(loss.detach()) * len(values)
            samples += len(values)
        losses.append({"epoch": epoch + 1, "loss": total / samples})
    return losses


def _save(model, spec, losses, output_dir):
    import pyarrow as pa
    import torch

    output_dir.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    torch.save(model.cpu().state_dict(), buffer)
    path = output_dir / "weights.pt"
    path.write_bytes(buffer.getvalue())
    return {
        "weights": artifact(path, "pytorch-state-dict"),
        "modelSpec": write_json(spec, output_dir / "model.json"),
        "loss": write_table(pa.Table.from_pylist(losses), output_dir / "loss.parquet"),
    }


def pretrain(_identity, inputs, parameters, output_dir):
    features, targets = array(inputs, "features", 3), array(inputs, "targets", 2)
    _seed(parameters["seed"])
    spec = {
        "schemaVersion": "gru-attention-model.v1",
        "inputWindow": features.shape[1],
        "inputFeatures": features.shape[2],
        **{name: parameters[name] for name in ("hiddenSize", "numLayers", "numHeads", "dropout")},
    }
    model = _model(spec)
    return _save(model, spec, _train(model, features, targets, parameters), output_dir)


def finetune(_identity, inputs, parameters, output_dir):
    features, targets = array(inputs, "features", 3), array(inputs, "targets", 2)
    model, spec = _restore(inputs, features)
    # Initialization is discarded; the old fine-tune starts with an already constructed model.
    _seed(parameters["seed"])
    return _save(model, spec, _train(model, features, targets, parameters), output_dir)


def predict(_identity, inputs, parameters, output_dir):
    import pyarrow as pa
    import torch

    _seed(0)
    features = array(inputs, "features", 3)
    index = table(inputs, "sampleIndex")
    if index.num_rows != len(features) or "sample_id" not in index.column_names:
        raise ValueError("SAMPLE_INDEX_INVALID")
    if index["sample_id"].to_pylist() != list(range(len(features))):
        raise ValueError("SAMPLE_INDEX_INVALID")
    if "predicted" in index.column_names:
        raise ValueError("OUTPUT_FIELD_CONFLICT")
    model, _ = _restore(inputs, features)
    model.eval()
    with torch.no_grad():
        predictions = model(torch.from_numpy(features)).numpy().reshape(-1)
    if not np.isfinite(predictions).all():
        raise ValueError("PREDICTION_NON_FINITE")
    result = index.append_column("predicted", pa.array(predictions))
    return {"predictions": write_table(result, output_dir / "predictions.parquet")}
