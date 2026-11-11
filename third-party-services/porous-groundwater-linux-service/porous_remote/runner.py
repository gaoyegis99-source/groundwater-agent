"""Fixed child-process entry point for one operation."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from . import PORTS
from .agriculture import invert_agriculture
from .calibration import calibrate
from .inversion import sample, train
from .packages import write_json
from .maps import prepare_map
from .results import analyze, render
from .scenarios import compare_scenarios
from .science import compile_model, simulate


def main() -> None:
    operation, artifacts, directory, vendor_dir, signing_key = sys.argv[1:]
    job, vendor = map(Path, (directory, vendor_dir))
    ports = PORTS[operation]
    sources = (
        {port: Path(source) for port, source in json.loads(artifacts).items()}
        if artifacts.startswith("{") else {next(iter(ports)): Path(artifacts)}
    )
    if set(sources) != set(ports):
        raise ValueError("OPERATION_INPUT_INVALID")
    key = Path(signing_key).read_bytes()
    sys.path.insert(0, str(vendor / "src"))
    match operation:
        case "model.compile":
            result = compile_model(sources["project_package"], job, vendor, key)
        case "flow_transport.simulate":
            result = simulate(sources["model_package"], job, vendor, key)
        case "result.analyze":
            result = analyze(sources["result_package"], job, key)
        case "result.render":
            result = render(sources["result_package"], job, key)
        case "scenario.compare":
            result = compare_scenarios(sources["scenario_package"], job, vendor, key)
        case "map.prepare":
            result = prepare_map(sources["map_input"], job)
        case "pest.calibrate":
            result = calibrate(sources["model_package"], sources["observations"], job, vendor, Path(signing_key))
        case "inversion.train":
            result = train(sources["training_data"], job, key)
        case "inversion.sample":
            result = sample(sources["weights"], sources["sample_config"], job, key)
        case "agri.invert":
            result = invert_agriculture(sources["agricultural_data"], job, vendor, key)
        case _:
            raise ValueError("UNKNOWN_OPERATION")
    write_json(job / "values.json", result)


if __name__ == "__main__":
    main()
