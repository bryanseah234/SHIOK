import json
from pathlib import Path

path = Path(
    "X:/01 REPOSITORIES/SHIOK/raw/7c987511548de3a82da403cabca02702031e02ade8703a9e401417883dfeb702/building_points.geojson"
)
if path.exists():
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    features = data.get("features", [])
    print(f"HDB Building Points count: {len(features)}")

    # Calculate projection
    missing = 140000 - len(features)
    hours = (missing * 2.0) / 3600
    print(f"Postals missing: {missing}")
    print(f"Projected hours at 2.0s: {hours:.2f}")
