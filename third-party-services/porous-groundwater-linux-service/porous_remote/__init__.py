"""Groundwater v2.6.2 headless Linux operation service."""

SERVICE_ID = "porous-groundwater"
VERSION = {
    "apiVersion": "remote-operation.v1",
    "serviceVersion": "1.1.1",
    "modelVersion": "porous-groundwater-v2.6.2-linux.v2",
}
PORTS = {
    "model.compile": {"project_package": "zip"},
    "flow_transport.simulate": {"model_package": "zip"},
    "result.analyze": {"result_package": "zip"},
    "result.render": {"result_package": "zip"},
    "scenario.compare": {"scenario_package": "zip"},
    "map.prepare": {"map_input": "geotiff"},
    "pest.calibrate": {"model_package": "zip", "observations": "json"},
    "inversion.train": {"training_data": "zip"},
    "inversion.sample": {"weights": "binary", "sample_config": "json"},
    "agri.invert": {"agricultural_data": "zip"},
}
MEDIA_TYPES = {
    "zip": "application/zip", "json": "application/json", "png": "image/png",
    "geotiff": "image/tiff",
    "binary": "application/octet-stream", "npy": "application/x-npy",
}
