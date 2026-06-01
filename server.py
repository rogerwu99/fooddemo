#!/usr/bin/env python3
import cgi
import json
import mimetypes
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import pillow_heif
    from PIL import Image
except ImportError:
    pillow_heif = None
    Image = None


ROOT = Path(__file__).resolve().parent
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
USDA_SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"

NUTRITION_DB = {
    "broccoli": {
        "name": "Broccoli",
        "serving": "1.5 cups cooked",
        "calories": 82,
        "protein": "5 g",
        "fiber": "7 g",
        "sugar": "0 g added",
        "points": 50,
        "why": "High fiber, vitamin C, potassium, and volume per calorie make this a strong satiety play.",
        "effect": "Strong lean gain",
    },
    "chicken": {
        "name": "Grilled chicken",
        "serving": "5 oz cooked",
        "calories": 235,
        "protein": "44 g",
        "fiber": "0 g",
        "sugar": "0 g added",
        "points": 20,
        "why": "Dense protein helps recovery and keeps the meal satisfying, especially with vegetables or grains.",
        "effect": "Muscle support",
    },
    "berries": {
        "name": "Berry bowl",
        "serving": "1.25 cups",
        "calories": 105,
        "protein": "2 g",
        "fiber": "8 g",
        "sugar": "0 g added",
        "points": 38,
        "why": "Fiber, antioxidants, and low energy density make berries a high-return snack or side.",
        "effect": "Lean boost",
    },
    "oatmeal": {
        "name": "Oatmeal",
        "serving": "1 cup cooked",
        "calories": 154,
        "protein": "6 g",
        "fiber": "4 g",
        "sugar": "0 g added",
        "points": 32,
        "why": "Oatmeal brings slow-digesting carbohydrates and soluble fiber, especially useful when paired with fruit or protein.",
        "effect": "Balanced shift",
    },
    "eggplant_parmesan": {
        "name": "Eggplant parmesan",
        "serving": "1 cup / 1 entree portion",
        "calories": 340,
        "protein": "15 g",
        "fiber": "7 g",
        "sugar": "6 g added",
        "points": 26,
        "why": "Eggplant parmesan can offer fiber and plant volume, while cheese, breading, and sauce keep the score moderate.",
        "effect": "Balanced shift",
    },
    "chicken_parmesan": {
        "name": "Chicken parmesan",
        "serving": "1 cutlet / 1 entree portion",
        "calories": 520,
        "protein": "42 g",
        "fiber": "3 g",
        "sugar": "5 g added",
        "points": 17,
        "why": "Chicken parmesan is protein-rich, but breading, cheese, and sauce make it more calorie-dense.",
        "effect": "Softness gain",
    },
    "pizza": {
        "name": "Pizza slice",
        "serving": "1 large slice",
        "calories": 310,
        "protein": "13 g",
        "fiber": "2 g",
        "sugar": "2 g added",
        "points": 8,
        "why": "Useful for enjoyment and quick energy, but refined flour and saturated fat keep the score modest.",
        "effect": "Softness gain",
    },
    "cake": {
        "name": "Chocolate cake",
        "serving": "1 slice",
        "calories": 430,
        "protein": "5 g",
        "fiber": "3 g",
        "sugar": "34 g added",
        "points": 1,
        "why": "Great as a treat. The high added sugar and low micronutrient density mean it barely moves the score.",
        "effect": "Chubby shift",
    },
    "mixed": {
        "name": "Mixed plate",
        "serving": "1 photographed plate",
        "calories": 360,
        "protein": "18 g",
        "fiber": "5 g",
        "sugar": "6 g added",
        "points": 24,
        "why": "The plate appears mixed or visually ambiguous, so this estimate uses a balanced default until a stronger classifier confirms the item.",
        "effect": "Balanced shift",
    },
}


def filename_hint(file_name):
    lower = (file_name or "").lower()
    if "broccoli" in lower or "green" in lower:
        return "broccoli"
    if "chicken" in lower or "protein" in lower:
        return "chicken"
    if "berry" in lower or "fruit" in lower:
        return "berries"
    if "oat" in lower or "porridge" in lower:
        return "oatmeal"
    if "cake" in lower or "dessert" in lower:
        return "cake"
    if "pizza" in lower:
        return "pizza"
    if "eggplant" in lower:
        return "eggplant_parmesan"
    if "parmesan" in lower or "parm" in lower:
        return "chicken_parmesan"
    return "mixed"


def score_candidates(signals, file_name):
    signals = signals or {}
    scores = {key: 0.05 for key in NUTRITION_DB.keys()}
    green = float(signals.get("green", 0))
    red = float(signals.get("red", 0))
    tan = float(signals.get("tan", 0))
    brown = float(signals.get("brown", 0))
    purple = float(signals.get("purple", 0))
    bright = float(signals.get("bright", 0))
    dark = float(signals.get("dark", 0))

    scores["broccoli"] += green * 2.8 + max(0, 0.24 - tan) * 0.8
    scores["berries"] += purple * 2.2 + red * 1.3 + bright * 0.35
    scores["oatmeal"] += tan * 1.45 + bright * 0.25 + max(0, 0.2 - red) * 0.35
    scores["pizza"] += red * 1.4 + tan * 1.25 + brown * 0.25
    scores["cake"] += brown * 1.8 + dark * 0.95 + tan * 0.35
    scores["chicken"] += tan * 1.8 + max(0, 0.16 - green) * 0.75 + max(0, 0.18 - red) * 0.4
    scores["eggplant_parmesan"] += tan * 0.8 + red * 0.8 + brown * 0.5 + green * 0.35
    scores["chicken_parmesan"] += tan * 1.0 + red * 0.75 + brown * 0.45
    scores["mixed"] += 0.32

    hint = filename_hint(file_name)
    scores[hint] += 0.25
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    total = sum(value for _, value in ranked) or 1
    return [
        {"key": key, "confidence": round(max(0.05, min(0.94, value / total)), 2)}
        for key, value in ranked
    ]


def build_nutrition_result(payload):
    file_name = payload.get("fileName", "")
    signals = payload.get("signals", {})
    source_format = payload.get("sourceFormat", "image")
    converted = bool(payload.get("converted"))
    image_data_url = payload.get("imageDataUrl", "")
    vision = analyze_with_openai_vision(image_data_url, file_name)
    ranked = rank_from_vision(vision) if vision else score_candidates(signals, file_name)
    components = components_from_vision(vision) if vision else []
    if not components:
        primary = ranked[0]
        components = [
            {
                "key": primary["key"],
                "label": primary.get("label") or NUTRITION_DB[primary["key"]]["name"],
                "query": primary.get("query") or NUTRITION_DB[primary["key"]]["name"],
                "serving_estimate": NUTRITION_DB[primary["key"]]["serving"],
                "confidence": primary["confidence"],
                "portion": 1,
            }
        ]

    component_foods = []
    for component in components[:6]:
        component_food = lookup_nutrition(
            component.get("query") or component.get("label") or NUTRITION_DB[component["key"]]["name"],
            component["key"],
        )
        component_food["name"] = component.get("label") or component_food["name"]
        component_food["serving"] = component.get("serving_estimate") or component_food["serving"]
        component_food["confidence"] = component.get("confidence", 0.5)
        component_food["portion"] = component.get("portion", 1)
        component_foods.append(component_food)

    food = combine_components(component_foods, vision.get("dish_name") if vision else "")
    primary = ranked[0]
    key = primary["key"]
    food["key"] = key
    food["confidence"] = round(sum(item.get("confidence", 0) for item in component_foods) / max(1, len(component_foods)), 2)
    food["alternatives"] = [
        {
            "key": item["key"],
            "name": item.get("label") or NUTRITION_DB[item["key"]]["name"],
            "confidence": item["confidence"],
        }
        for item in ranked[1:4]
    ]
    food["pipeline"] = {
        "steps": [
            "File accepted",
            "HEIC/HEIF converted to JPEG" if converted else "Image decoded in browser",
            "OpenAI vision identified food candidates" if vision else "Browser extracted color and brightness signals",
            "Server normalized candidates",
            "USDA FoodData Central lookup" if food.get("databaseSource") == "USDA FoodData Central" else "Local nutrition fallback",
            "PlatePoints score calculated",
        ],
        "sourceFormat": source_format,
        "converted": converted,
        "signals": signals,
        "visionProvider": "OpenAI Responses API" if vision else "Local signal heuristic",
        "nutritionProvider": food.get("databaseSource", "Local nutrition fallback"),
        "notes": food.get("databaseNotes", []),
        "componentCount": len(component_foods),
    }
    return food


def post_json(url, payload, headers=None, timeout=30):
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def analyze_with_openai_vision(image_data_url, file_name):
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key or not image_data_url:
        return None

    prompt = (
        "Analyze this food photo for a nutrition logging app. Return JSON only with this shape: "
        '{"foods":[{"label":"string","canonical":"broccoli|chicken|berries|oatmeal|eggplant_parmesan|chicken_parmesan|pizza|cake|mixed",'
        '"query":"plain USDA food search query","serving_estimate":"short serving estimate",'
        '"portion":0.0,"confidence":0.0}],"dish_name":"string","notes":["short note"]}. '
        "Identify the complete dish and visible components, not only the most colorful ingredient. "
        "For example, oatmeal with blueberries should return dish_name='oatmeal with blueberries' "
        "and separate foods for oatmeal and blueberries. Portion is the approximate share of the dish "
        "from 0 to 1. Prefer common food names that can be searched in USDA FoodData Central. "
        "For visually similar dishes, include plausible alternatives with confidence scores, such as eggplant parmesan versus chicken parmesan. "
        "Use canonical=mixed only when a component does not fit the known canonical set."
    )
    payload = {
        "model": os.environ.get("OPENAI_VISION_MODEL", "gpt-4.1-mini"),
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": image_data_url, "detail": "high"},
                ],
            }
        ],
    }

    try:
        response = post_json(
            OPENAI_RESPONSES_URL,
            payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=45,
        )
        text = extract_response_text(response)
        return json.loads(text)
    except Exception as error:
        return {"error": str(error), "foods": []}


def extract_response_text(response):
    if response.get("output_text"):
        return response["output_text"]
    chunks = []
    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                chunks.append(content["text"])
    text = "\n".join(chunks).strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.replace("json\n", "", 1).strip()
    return text or "{}"


def rank_from_vision(vision):
    foods = vision.get("foods") or []
    if not foods:
        return score_candidates({}, "")

    ranked = []
    for item in foods:
        canonical = item.get("canonical", "mixed")
        if canonical not in NUTRITION_DB:
            canonical = "mixed"
        ranked.append(
            {
                "key": canonical,
                "label": item.get("label") or NUTRITION_DB[canonical]["name"],
                "query": item.get("query") or item.get("label") or NUTRITION_DB[canonical]["name"],
                "confidence": round(max(0.05, min(0.96, float(item.get("confidence", 0.5)))), 2),
            }
        )

    seen = set()
    unique = []
    for item in sorted(ranked, key=lambda entry: entry["confidence"], reverse=True):
        marker = (item["key"], item["label"].lower())
        if marker not in seen:
            unique.append(item)
            seen.add(marker)
    for key in NUTRITION_DB:
        if all(item["key"] != key for item in unique):
            unique.append({"key": key, "confidence": 0.05})
    return unique


def components_from_vision(vision):
    foods = vision.get("foods") or []
    components = []
    for item in foods:
        canonical = item.get("canonical", "mixed")
        if canonical not in NUTRITION_DB:
            canonical = "mixed"
        try:
            portion = float(item.get("portion", 1))
        except (TypeError, ValueError):
            portion = 1
        components.append(
            {
                "key": canonical,
                "label": item.get("label") or item.get("query") or NUTRITION_DB[canonical]["name"],
                "query": item.get("query") or item.get("label") or NUTRITION_DB[canonical]["name"],
                "serving_estimate": item.get("serving_estimate") or NUTRITION_DB[canonical]["serving"],
                "portion": max(0.05, min(1.0, portion)),
                "confidence": round(max(0.05, min(0.96, float(item.get("confidence", 0.5)))), 2),
            }
        )
    return components


def combine_components(component_foods, dish_name=""):
    if not component_foods:
        return dict(NUTRITION_DB["mixed"])

    names = [food["name"] for food in component_foods]
    if dish_name:
        name = dish_name
    elif len(names) == 1:
        name = names[0]
    elif len(names) == 2:
        name = f"{names[0]} with {names[1]}"
    else:
        name = f"{', '.join(names[:-1])}, and {names[-1]}"

    calories = sum(float(food.get("calories", 0)) for food in component_foods)
    protein = sum(parse_grams(food.get("protein", "0 g")) for food in component_foods)
    fiber = sum(parse_grams(food.get("fiber", "0 g")) for food in component_foods)
    sugar = sum(parse_grams(food.get("sugar", "0 g")) for food in component_foods)
    db_sources = sorted({food.get("databaseSource", "Local nutrition fallback") for food in component_foods})
    notes = []
    for food in component_foods:
        notes.extend(food.get("databaseNotes", []))

    combined = {
        "name": name,
        "serving": " + ".join(food.get("serving", "estimated serving") for food in component_foods),
        "calories": round(calories),
        "protein": f"{protein:.0f} g",
        "fiber": f"{fiber:.0f} g",
        "sugar": f"{sugar:.0f} g",
        "why": "This looks like a composite meal, so the pipeline identifies visible components and combines their estimated nutrition.",
        "databaseSource": " + ".join(db_sources),
        "databaseNotes": dedupe(notes)[:5],
        "components": [
            {
                "name": food["name"],
                "serving": food.get("serving", "estimated serving"),
                "calories": food.get("calories", 0),
                "confidence": food.get("confidence", 0.5),
            }
            for food in component_foods
        ],
    }
    combined["points"] = calculate_points(combined)
    combined["effect"] = effect_for_points(combined["points"])
    return combined


def parse_grams(value):
    try:
        return float(str(value).split()[0])
    except (TypeError, ValueError, IndexError):
        return 0.0


def dedupe(items):
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def lookup_nutrition(query, fallback_key):
    base = dict(NUTRITION_DB.get(fallback_key, NUTRITION_DB["mixed"]))
    api_key = os.environ.get("USDA_API_KEY", "").strip()
    if not api_key:
        base["databaseSource"] = "Local nutrition fallback"
        base["databaseNotes"] = ["Set USDA_API_KEY to enable FoodData Central lookup."]
        return base

    try:
        search_url = f"{USDA_SEARCH_URL}?{urllib.parse.urlencode({'api_key': api_key})}"
        result = post_json(
            search_url,
            {
                "query": query,
                "pageSize": 5,
                "pageNumber": 1,
                "dataType": ["Foundation", "SR Legacy", "Survey (FNDDS)", "Branded"],
            },
            timeout=20,
        )
        foods = result.get("foods") or []
        if not foods:
            raise ValueError("No USDA matches")
        match = foods[0]
        nutrients = extract_usda_nutrients(match.get("foodNutrients", []))
        calories = nutrients.get("calories")
        protein = nutrients.get("protein")
        fiber = nutrients.get("fiber")
        sugar = nutrients.get("added_sugar") or nutrients.get("sugar")
        if calories is not None:
            base["calories"] = round(calories)
        if protein is not None:
            base["protein"] = f"{protein:.0f} g"
        if fiber is not None:
            base["fiber"] = f"{fiber:.0f} g"
        if sugar is not None:
            base["sugar"] = f"{sugar:.0f} g"
        base["serving"] = match.get("servingSizeUnit") and match.get("servingSize") and (
            f"{match.get('servingSize')} {match.get('servingSizeUnit')}"
        ) or base["serving"]
        base["databaseSource"] = "USDA FoodData Central"
        base["databaseNotes"] = [
            f"Matched FDC ID {match.get('fdcId')} ({match.get('dataType', 'unknown type')}).",
            "USDA values may be per serving or per 100 g depending on record metadata.",
        ]
        base["points"] = calculate_points(base)
        base["effect"] = effect_for_points(base["points"])
        return base
    except Exception as error:
        base["databaseSource"] = "Local nutrition fallback"
        base["databaseNotes"] = [f"USDA lookup failed: {error}"]
        return base


def extract_usda_nutrients(food_nutrients):
    values = {}
    for nutrient in food_nutrients:
        name = (nutrient.get("nutrientName") or nutrient.get("name") or "").lower()
        value = nutrient.get("value")
        if value is None:
            continue
        if "energy" in name or "calorie" in name:
            values.setdefault("calories", float(value))
        elif name == "protein":
            values["protein"] = float(value)
        elif "fiber" in name:
            values["fiber"] = float(value)
        elif "added sugar" in name:
            values["added_sugar"] = float(value)
        elif "sugars" in name:
            values.setdefault("sugar", float(value))
    return values


def calculate_points(food):
    try:
        protein = float(str(food.get("protein", "0")).split()[0])
        fiber = float(str(food.get("fiber", "0")).split()[0])
        sugar = float(str(food.get("sugar", "0")).split()[0])
        calories = float(food.get("calories", 0))
    except ValueError:
        return food.get("points", 24)
    score = 18 + protein * 0.4 + fiber * 3 - sugar * 0.7 - max(0, calories - 350) * 0.025
    return int(max(1, min(50, round(score))))


def effect_for_points(points):
    if points >= 38:
        return "Strong lean gain"
    if points >= 18:
        return "Balanced shift"
    if points >= 8:
        return "Softness gain"
    return "Chubby shift"


def convert_heic_bytes(source, output):
    if pillow_heif and Image:
        pillow_heif.register_heif_opener()
        with Image.open(source) as image:
            image.convert("RGB").save(output, "JPEG", quality=92)
        return

    if shutil.which("sips"):
        result = subprocess.run(
            ["sips", "-s", "format", "jpeg", str(source), "--out", str(output)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0 or not output.exists():
            raise RuntimeError((result.stderr or result.stdout).strip()[:400])
        return

    raise RuntimeError("No HEIC converter available. Install pillow-heif for staging.")


class PlatePointsHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_POST(self):
        if self.path == "/convert-heic":
            self._convert_heic()
            return
        if self.path == "/nutrition-pipeline":
            self._nutrition_pipeline()
            return

        self.send_error(404, "Unknown endpoint")

    def _nutrition_pipeline(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            self._send_json(build_nutrition_result(payload))
        except Exception as error:
            self._send_json({"error": "Nutrition pipeline failed", "details": str(error)}, status=400)

    def _convert_heic(self):
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            },
        )
        upload = form["image"] if "image" in form else None
        if upload is None or not getattr(upload, "filename", ""):
            self._send_json({"error": "No image uploaded"}, status=400)
            return

        suffix = Path(upload.filename).suffix.lower() or ".heic"
        if suffix not in {".heic", ".heif"}:
            self._send_json({"error": "Only HEIC or HEIF files are converted here"}, status=400)
            return

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / f"upload{suffix}"
            output = Path(temp_dir) / "converted.jpg"

            with source.open("wb") as source_file:
                shutil.copyfileobj(upload.file, source_file)

            try:
                convert_heic_bytes(source, output)
            except Exception as error:
                self._send_json(
                    {
                        "error": "HEIC conversion failed",
                        "details": str(error)[:400],
                    },
                    status=422,
                )
                return

            data = output.read_bytes()

        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload, status=200):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def guess_type(self, path):
        if path.endswith(".heic"):
            return "image/heic"
        if path.endswith(".heif"):
            return "image/heif"
        return mimetypes.guess_type(path)[0] or "application/octet-stream"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8123"))
    host = os.environ.get("HOST", "0.0.0.0")
    server = ThreadingHTTPServer((host, port), PlatePointsHandler)
    print(f"PlatePoints running at http://{host}:{port}")
    server.serve_forever()
