import json, glob
for f in glob.glob("ml/reports/*.json"):
    try:
        with open(f, "r") as fp:
            d = json.load(fp)
            if isinstance(d, dict) and ("phoneos_markers" in d or "counts" in d):
                print(f, d.get("counts"))
    except Exception as e:
        pass
