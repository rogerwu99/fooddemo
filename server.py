#!/usr/bin/env python3
import json
import mimetypes
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from email.parser import BytesParser
from email.policy import default
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


def load_dotenv(path):
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv(ROOT / ".env")


def public_supabase_url():
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    for suffix in ("/rest/v1", "/auth/v1", "/storage/v1"):
        if url.endswith(suffix):
            return url[: -len(suffix)]
    return url


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
    "pasta": {
        "name": "Cacio e pepe",
        "serving": "1.5 cups",
        "calories": 540,
        "protein": "19 g",
        "fiber": "3 g",
        "sugar": "0 g added",
        "points": 14,
        "why": "Pasta provides quick energy and some protein from cheese, while refined pasta, butter, and cheese keep the score moderate.",
        "effect": "Softness gain",
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
    "syrup": {
        "name": "Syrup",
        "serving": "1 tablespoon",
        "calories": 52,
        "protein": "0 g",
        "fiber": "0 g",
        "sugar": "12 g added",
        "points": 5,
        "why": "Syrup mostly contributes added sugar, so a small amount should reduce the score without erasing the value of the whole meal.",
        "effect": "Softness gain",
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
    if "cacio" in lower or "pepe" in lower or "pasta" in lower or "spaghetti" in lower or "noodle" in lower:
        return "pasta"
    if "cake" in lower or "dessert" in lower:
        return "cake"
    if "syrup" in lower or "maple" in lower or "honey" in lower:
        return "syrup"
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
    scores["berries"] += purple * 2.8 + red * 1.15 + bright * 0.25
    scores["oatmeal"] += tan * 1.3 + bright * 0.2 + max(0, 0.28 - green) * 0.18
    scores["pasta"] += tan * 2.2 + bright * 0.25 + max(0, 0.18 - red) * 0.3
    scores["pizza"] += red * 1.4 + tan * 1.25 + brown * 0.25
    scores["cake"] += brown * 1.1 + dark * 0.9 + max(0, tan - 0.26) * 0.2
    if tan > 0.14 and (purple > 0.03 or red > 0.05 or bright > 0.2):
        scores["oatmeal"] += 0.45
        scores["cake"] *= 0.55
    scores["syrup"] += brown * 0.55 + bright * 0.2
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


def oatmeal_bowl_components_from_signals(signals, file_name, current_components=None):
    signals = signals or {}
    current_components = current_components or []
    current_keys = {component.get("key") for component in current_components}
    if current_keys and current_keys - {"cake", "mixed"}:
        return []

    hint = filename_hint(file_name)
    tan = float(signals.get("tan", 0))
    brown = float(signals.get("brown", 0))
    purple = float(signals.get("purple", 0))
    red = float(signals.get("red", 0))
    bright = float(signals.get("bright", 0))
    dark = float(signals.get("dark", 0))
    food_pixels = float(signals.get("foodPixels", 0))
    green = float(signals.get("green", 0))
    fruit_signal = purple > 0.018 or red > 0.045
    text_signal = hint == "oatmeal" or any(
        word in (file_name or "").lower()
        for word in ["oat", "oatmeal", "porridge", "blueberr", "berry"]
    )
    oatmeal_signal = hint == "oatmeal" or (
        (tan > 0.075 or (brown > 0.12 and bright > 0.08))
        and green < 0.18
        and dark < 0.38
        and (fruit_signal or text_signal)
    )
    cake_signal = brown > 0.28 and dark > 0.2 and not fruit_signal and hint != "oatmeal"
    if not oatmeal_signal or cake_signal:
        return []

    components = [
        {
            "key": "oatmeal",
            "label": "Oatmeal",
            "query": "cooked oatmeal",
            "serving_estimate": "1 cup cooked",
            "role": "base",
            "nutrient_role": "fiber",
            "portion": 0.65,
            "confidence": 0.68,
        }
    ]
    if fruit_signal:
        components.append(
            {
                "key": "berries",
                "label": "Blueberries",
                "query": "blueberries raw",
                "serving_estimate": "1/2 cup",
                "role": "fruit_veg",
                "nutrient_role": "fiber",
                "portion": 0.25,
                "confidence": 0.62,
            }
        )
    if brown > 0.08 and bright > 0.12:
        components.append(
            {
                "key": "syrup",
                "label": "Syrup",
                "query": "maple syrup",
                "serving_estimate": "1 tablespoon",
                "role": "sweetener",
                "nutrient_role": "added_sugar",
                "portion": 0.08,
                "confidence": 0.45,
            }
        )
    return components


def name_for_components(components):
    keys = {component.get("key") for component in components}
    if {"oatmeal", "berries", "syrup"}.issubset(keys):
        return "Oatmeal with blueberries and syrup"
    if {"oatmeal", "berries"}.issubset(keys):
        return "Oatmeal with blueberries"
    return NUTRITION_DB[components[0]["key"]]["name"] if components else ""


def promote_ranked_key(ranked, key):
    promoted = []
    found = False
    for item in ranked:
        if item.get("key") == key:
            promoted.insert(0, {**item, "confidence": max(item.get("confidence", 0.5), 0.72)})
            found = True
        else:
            promoted.append(item)
    if not found:
        promoted.insert(0, {"key": key, "confidence": 0.72})
    return promoted


def build_nutrition_result(payload):
    file_name = payload.get("fileName", "")
    signals = payload.get("signals", {})
    source_format = payload.get("sourceFormat", "image")
    converted = bool(payload.get("converted"))
    image_data_url = payload.get("imageDataUrl", "")
    vision = analyze_with_openai_vision(image_data_url, file_name)
    if not vision:
        return analysis_unavailable_result(
            "Vision analysis is not configured. Set OPENAI_API_KEY to analyze uploaded food photos.",
            source_format,
            converted,
            signals,
        )
    if vision.get("error"):
        return analysis_unavailable_result(
            f"Vision analysis failed: {vision.get('error')}",
            source_format,
            converted,
            signals,
        )
    if is_non_food_vision(vision):
        return non_food_result(vision, source_format, converted, signals)
    ranked = rank_from_vision(vision)
    components = components_from_vision(vision)
    components = expand_composite_components(components, vision)
    if not components:
        return analysis_unavailable_result(
            "Vision analysis did not return any food components.",
            source_format,
            converted,
            signals,
        )

    component_foods = []
    for component in components[:6]:
        component_food = lookup_nutrition(
            component.get("query") or component.get("label") or NUTRITION_DB[component["key"]]["name"],
            component["key"],
        )
        component_food["key"] = component["key"]
        component_food["name"] = component.get("label") or component_food["name"]
        component_food["serving"] = component.get("serving_estimate") or component_food["serving"]
        component_food["confidence"] = component.get("confidence", 0.5)
        component_food["portion"] = component.get("portion", 1)
        component_food["role"] = component.get("role") or infer_component_role(
            component["key"],
            component.get("label", ""),
            component.get("query", ""),
        )
        component_food["nutrient_role"] = component.get("nutrient_role") or infer_nutrient_role(
            component["key"],
            component.get("label", ""),
            component.get("query", ""),
        )
        component_foods.append(component_food)

    food = combine_components(component_foods, vision.get("dish_name", ""))
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
            "OpenAI vision identified food candidates",
            "Server normalized candidates",
            "USDA FoodData Central lookup" if food.get("databaseSource") == "USDA FoodData Central" else "Local nutrition fallback",
            "PlatePoints score calculated",
        ],
        "sourceFormat": source_format,
        "converted": converted,
        "signals": signals,
        "visionProvider": "OpenAI Responses API",
        "nutritionProvider": food.get("databaseSource", "Local nutrition fallback"),
        "notes": food.get("databaseNotes", []),
        "componentCount": len(component_foods),
    }
    return food


def build_manual_correction_result(payload):
    label = (payload.get("label") or payload.get("food") or "").strip()
    if not label:
        return analysis_unavailable_result(
            "Enter the corrected food name before looking up nutrition.",
            "manual correction",
            False,
            {},
        )

    key = normalize_component_key(payload.get("key", "mixed"), label, label)
    if key == "mixed" and not os.environ.get("USDA_API_KEY", "").strip():
        return analysis_unavailable_result(
            "USDA_API_KEY is required to look up arbitrary manual food corrections.",
            "manual correction",
            False,
            {},
        )

    component = {
        "key": key,
        "label": label,
        "query": label,
        "serving_estimate": payload.get("serving") or NUTRITION_DB.get(key, NUTRITION_DB["mixed"])["serving"],
        "role": infer_component_role(key, label, label),
        "nutrient_role": infer_nutrient_role(key, label, label),
        "portion": 1,
        "confidence": 1,
    }
    component_food = lookup_nutrition(component["query"], component["key"])
    component_food["key"] = component["key"]
    component_food["name"] = component["label"]
    component_food["serving"] = component["serving_estimate"] or component_food["serving"]
    component_food["confidence"] = 1
    component_food["portion"] = 1
    component_food["role"] = component["role"]
    component_food["nutrient_role"] = component["nutrient_role"]

    food = combine_components([component_food], label)
    food["key"] = key
    food["confidence"] = 1
    food["alternatives"] = []
    food["loggable"] = True
    food["pipeline"] = {
        "steps": [
            "User corrected food identity",
            "Server looked up corrected food",
            "USDA FoodData Central lookup" if food.get("databaseSource") == "USDA FoodData Central" else "Local nutrition fallback",
            "PlatePoints score recalculated",
        ],
        "sourceFormat": "manual correction",
        "converted": False,
        "signals": {},
        "visionProvider": "Manual correction",
        "nutritionProvider": food.get("databaseSource", "Local nutrition fallback"),
        "notes": food.get("databaseNotes", []),
        "componentCount": 1,
    }
    return food


def is_non_food_vision(vision):
    if not vision:
        return False
    if vision.get("is_food") is False:
        return True
    foods = vision.get("foods")
    if foods == []:
        return True
    if foods:
        confidences = []
        canonical_values = set()
        for item in foods:
            canonical_values.add(item.get("canonical", "mixed"))
            try:
                confidences.append(float(item.get("confidence", 0)))
            except (TypeError, ValueError):
                confidences.append(0)
        max_confidence = max(confidences or [0])
        if canonical_values == {"mixed"} and max_confidence < 0.45 and not vision.get("dish_name"):
            return True
    return False


def non_food_result(vision, source_format, converted, signals):
    confidence = vision.get("confidence", 0.0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0
    return {
        "name": "No food detected",
        "serving": "-",
        "calories": "-",
        "protein": "-",
        "fiber": "-",
        "sugar": "-",
        "points": 0,
        "why": "We could not identify a food item in this photo. Try another image with the meal clearly visible.",
        "effect": "Avatar unchanged",
        "confidence": round(max(0, min(1, confidence)), 2),
        "alternatives": [],
        "components": [],
        "loggable": False,
        "pipeline": {
            "steps": [
                "File accepted",
                "HEIC/HEIF converted to JPEG" if converted else "Image decoded in browser",
                "OpenAI vision checked for food",
                "No loggable food detected",
            ],
            "sourceFormat": source_format,
            "converted": converted,
            "signals": signals,
            "visionProvider": "OpenAI Responses API",
            "nutritionProvider": "Skipped",
            "notes": vision.get("notes", []),
        },
    }


def analysis_unavailable_result(message, source_format, converted, signals):
    return {
        "name": "Analysis unavailable",
        "serving": "-",
        "calories": "-",
        "protein": "-",
        "fiber": "-",
        "sugar": "-",
        "points": 0,
        "why": message,
        "effect": "Avatar unchanged",
        "confidence": 0,
        "alternatives": [],
        "components": [],
        "loggable": False,
        "pipeline": {
            "steps": [
                "File accepted",
                "HEIC/HEIF converted to JPEG" if converted else "Image decoded in browser",
                "Vision/database pipeline unavailable",
                "No guessed food was logged",
            ],
            "sourceFormat": source_format,
            "converted": converted,
            "signals": signals,
            "visionProvider": "OpenAI Responses API",
            "nutritionProvider": "Skipped",
            "notes": [message],
        },
    }


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
        '{"is_food":true,"confidence":0.0,'
        '"foods":[{"label":"specific visible food or component","canonical":"broccoli|chicken|berries|oatmeal|syrup|pasta|eggplant_parmesan|chicken_parmesan|pizza|cake|mixed",'
        '"query":"plain USDA food search query","serving_estimate":"short serving estimate",'
        '"role":"base|protein|fruit_veg|mix_in|topping|sauce|condiment|dessert",'
        '"nutrient_role":"fiber|protein|added_sugar|fat|neutral",'
        '"portion":0.0,"confidence":0.0}],"dish_name":"string","notes":["short note"]}. '
        "If the image is not food, return is_food=false, confidence, foods=[], dish_name='', and notes explaining what was seen. "
        "Identify the complete dish and visible components, not only the most colorful ingredient. "
        "For example, oatmeal with blueberries and syrup should return dish_name='oatmeal with blueberries and syrup' "
        "and separate foods for oatmeal, blueberries, and syrup. Mark syrup, honey, butter, sauces, dressings, drizzles, and condiments as role='topping' or role='sauce'. Portion is the approximate share of the dish "
        "from 0 to 1. Prefer common food names that can be searched in USDA FoodData Central. "
        "For visually similar dishes, include plausible alternatives with confidence scores, such as eggplant parmesan versus chicken parmesan. "
        "Use canonical=mixed when a component does not fit the known canonical set, but keep label and query specific, for example cacio e pepe, salmon, salad, rice, dumplings, or burrito."
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
        return []

    ranked = []
    for item in foods:
        canonical = item.get("canonical", "mixed")
        if canonical not in NUTRITION_DB:
            canonical = "mixed"
        ranked.append(
            {
                "key": canonical,
                "label": item.get("label") or item.get("query") or NUTRITION_DB[canonical]["name"],
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
        original_label = item.get("label") or item.get("query") or ""
        original_query = item.get("query") or item.get("label") or ""
        canonical = normalize_component_key(item.get("canonical", "mixed"), original_label, original_query)
        try:
            portion = float(item.get("portion", 1))
        except (TypeError, ValueError):
            portion = 1
        if looks_like_combined_dish(original_label) and canonical in {"oatmeal", "berries", "syrup"}:
            original_label = NUTRITION_DB[canonical]["name"]
            original_query = "cooked oatmeal" if canonical == "oatmeal" else ("blueberries raw" if canonical == "berries" else "maple syrup")
            portion = default_component_portion(canonical)
        components.append(
            {
                "key": canonical,
                "label": original_label or NUTRITION_DB[canonical]["name"],
                "query": original_query or NUTRITION_DB[canonical]["name"],
                "serving_estimate": item.get("serving_estimate") or NUTRITION_DB[canonical]["serving"],
                "role": item.get("role") or infer_component_role(canonical, original_label, original_query),
                "nutrient_role": item.get("nutrient_role") or infer_nutrient_role(canonical, original_label, original_query),
                "portion": max(0.05, min(1.0, portion)),
                "confidence": round(max(0.05, min(0.96, float(item.get("confidence", 0.5)))), 2),
            }
        )
    return components


def looks_like_combined_dish(label):
    text = (label or "").lower()
    return " with " in text or " and " in text or "," in text


def default_component_portion(key):
    return {"oatmeal": 0.6, "berries": 0.25, "syrup": 0.1}.get(key, 1)


def infer_component_role(canonical, label="", query=""):
    text = f"{canonical} {label} {query}".lower()
    if any(word in text for word in ["syrup", "honey", "sugar", "sweetener", "jam", "jelly", "molasses"]):
        return "sweetener"
    if any(word in text for word in ["sauce", "dressing", "drizzle", "butter", "cream", "mayo", "aioli", "ranch", "glaze"]):
        return "topping"
    if canonical in {"berries", "broccoli"}:
        return "fruit_veg"
    if canonical in {"chicken"}:
        return "protein"
    if canonical in {"oatmeal"}:
        return "base"
    if canonical in {"pasta"}:
        return "base"
    if canonical in {"cake"}:
        return "dessert"
    return "mix_in"


def infer_nutrient_role(canonical, label="", query=""):
    text = f"{canonical} {label} {query}".lower()
    if any(word in text for word in ["syrup", "honey", "sugar", "jam", "jelly"]):
        return "added_sugar"
    if canonical in {"chicken"}:
        return "protein"
    if canonical in {"berries", "broccoli", "oatmeal"}:
        return "fiber"
    return "neutral"


def normalize_component_key(canonical, label="", query=""):
    text = f"{canonical} {label} {query}".lower()
    if "oat" in text or "porridge" in text:
        return "oatmeal"
    if "blueberr" in text or "berr" in text:
        return "berries"
    if "syrup" in text or "maple" in text or "honey" in text or "sweetener" in text:
        return "syrup"
    if "cacio" in text or "pepe" in text or "pasta" in text or "spaghetti" in text or "noodle" in text:
        return "pasta"
    if "eggplant" in text:
        return "eggplant_parmesan"
    if "chicken" in text and ("parmesan" in text or "parm" in text):
        return "chicken_parmesan"
    return canonical if canonical in NUTRITION_DB else "mixed"


def expand_composite_components(components, vision):
    if not vision:
        return components
    dish_text = " ".join(
        [
            str(vision.get("dish_name", "")),
            " ".join(str(item.get("label", "")) for item in vision.get("foods", [])),
            " ".join(str(item.get("query", "")) for item in vision.get("foods", [])),
        ]
    ).lower()
    keys = {component["key"] for component in components}

    inferred = []
    if ("oat" in dish_text or "porridge" in dish_text) and "oatmeal" not in keys:
        inferred.append({"key": "oatmeal", "label": "Oatmeal", "query": "cooked oatmeal", "serving_estimate": "1 cup cooked", "role": "base", "nutrient_role": "fiber", "portion": 0.6, "confidence": 0.7})
    if ("blueberr" in dish_text or "berr" in dish_text) and "berries" not in keys:
        inferred.append({"key": "berries", "label": "Blueberries", "query": "blueberries raw", "serving_estimate": "1/2 cup", "role": "fruit_veg", "nutrient_role": "fiber", "portion": 0.25, "confidence": 0.7})
    if ("syrup" in dish_text or "maple" in dish_text or "honey" in dish_text) and "syrup" not in keys:
        inferred.append({"key": "syrup", "label": "Syrup", "query": "maple syrup", "serving_estimate": "1 tablespoon", "role": "sweetener", "nutrient_role": "added_sugar", "portion": 0.1, "confidence": 0.65})

    if inferred and (len(components) == 1 and components[0]["key"] == "mixed"):
        return inferred
    return components + inferred


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

    weighted_components = [with_portion_weight(food) for food in component_foods]
    calories = sum(food["weightedCalories"] for food in weighted_components)
    protein = sum(food["weightedProtein"] for food in weighted_components)
    fiber = sum(food["weightedFiber"] for food in weighted_components)
    sugar = sum(food["weightedSugar"] for food in weighted_components)
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
        "why": build_component_why(component_foods),
        "databaseSource": " + ".join(db_sources),
        "databaseNotes": dedupe(notes)[:5],
        "components": [
            {
                "name": food["name"],
                "serving": food.get("serving", "estimated serving"),
                "role": food.get("role", "mix_in"),
                "portion": food["portionWeight"],
                "calories": round(food["weightedCalories"]),
                "confidence": food.get("confidence", 0.5),
                "why": short_component_why(food),
            }
            for food in weighted_components
        ],
    }
    combined["points"] = calculate_points(combined)
    combined["effect"] = effect_for_points(combined["points"])
    return combined


def with_portion_weight(food):
    weighted = dict(food)
    try:
        portion = float(food.get("portion", 1))
    except (TypeError, ValueError):
        portion = 1

    role = food.get("role") or infer_component_role(
        food.get("key", "mixed"),
        food.get("name", ""),
        food.get("serving", ""),
    )
    role_caps = {
        "sweetener": 0.16,
        "condiment": 0.16,
        "sauce": 0.22,
        "topping": 0.25,
        "mix_in": 0.45,
        "fruit_veg": 0.55,
        "base": 1.0,
        "protein": 1.0,
        "dessert": 1.0,
    }
    role_floors = {
        "sweetener": 0.05,
        "condiment": 0.04,
        "sauce": 0.05,
        "topping": 0.05,
        "mix_in": 0.1,
        "fruit_veg": 0.1,
        "base": 0.2,
        "protein": 0.2,
        "dessert": 0.2,
    }
    portion = min(portion, role_caps.get(role, 1.0))
    portion = max(role_floors.get(role, 0.1), min(1.0, portion))

    weighted["role"] = role
    weighted["portionWeight"] = portion
    weighted["weightedCalories"] = float(food.get("calories", 0)) * portion
    weighted["weightedProtein"] = parse_grams(food.get("protein", "0 g")) * portion
    weighted["weightedFiber"] = parse_grams(food.get("fiber", "0 g")) * portion
    weighted["weightedSugar"] = parse_grams(food.get("sugar", "0 g")) * portion
    if role in {"sweetener", "condiment", "sauce", "topping"}:
        weighted["weightedCalories"] = min(weighted["weightedCalories"], 70)
        weighted["weightedSugar"] = min(weighted["weightedSugar"], 14)
    if role == "sweetener":
        weighted["weightedCalories"] = min(weighted["weightedCalories"], 60)
        weighted["weightedSugar"] = min(weighted["weightedSugar"], 12)
    return weighted


def short_component_why(food):
    key_name = food.get("name", "").lower()
    if "oat" in key_name:
        return "slow-digesting carbohydrates and soluble fiber for fullness"
    if "blueberr" in key_name or "berr" in key_name:
        return "fiber, antioxidants, and sweetness with low calorie density"
    if "broccoli" in key_name:
        return "fiber, vitamin C, and high volume for very few calories"
    if "chicken" in key_name:
        return "lean protein for recovery and satiety"
    if "eggplant" in key_name:
        return "plant volume and fiber, balanced by cheese and sauce"
    if "pizza" in key_name:
        return "quick energy and enjoyment, but a modest nutrition score"
    if "cake" in key_name:
        return "best treated as a dessert because added sugar drives the score down"
    if "cacio" in key_name or "pasta" in key_name or "spaghetti" in key_name:
        return "quick energy from pasta with cheese adding protein and calorie density"
    if "syrup" in key_name or "honey" in key_name:
        return "sweetness and added sugar, so portion size matters"

    protein = parse_grams(food.get("protein", "0 g"))
    fiber = parse_grams(food.get("fiber", "0 g"))
    sugar = parse_grams(food.get("sugar", "0 g"))
    try:
        calories = float(food.get("calories", 0))
    except (TypeError, ValueError):
        calories = 0

    reasons = []
    if protein >= 15:
        reasons.append("meaningful protein for fullness")
    if fiber >= 4:
        reasons.append("fiber that supports satiety and steadier energy")
    if sugar >= 10:
        reasons.append("added sugar, so portion size matters")
    if calories >= 400:
        reasons.append("calorie density, which lowers the score")
    if not reasons:
        base_why = food.get("why", "").rstrip(".")
        if base_why and "pipeline" not in base_why.lower() and "ambiguous" not in base_why.lower():
            return base_why
        reasons.append("contributes to the overall meal balance")
    return ", and ".join(reasons)


def build_component_why(component_foods):
    if len(component_foods) <= 1:
        if not component_foods:
            return NUTRITION_DB["mixed"]["why"]
        food = component_foods[0]
        return f"{food['name']} adds {short_component_why(food)}."

    sentences = []
    for food in component_foods[:4]:
        verb = "add" if food["name"].lower().endswith("s") else "adds"
        sentences.append(f"{food['name']} {verb} {short_component_why(food)}.")
    sentences.append("Together, these choices determine the overall PlatePoints score.")
    return " ".join(sentences)


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


def parse_multipart_upload(headers, body, field_name="image"):
    content_type = headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type:
        raise ValueError("Expected multipart/form-data upload")

    message_bytes = (
        f"Content-Type: {content_type}\r\n"
        "MIME-Version: 1.0\r\n\r\n"
    ).encode("utf-8") + body
    message = BytesParser(policy=default).parsebytes(message_bytes)

    for part in message.iter_parts():
        disposition = part.get_content_disposition()
        if disposition != "form-data":
            continue
        params = dict(part.get_params(header="content-disposition") or [])
        if params.get("name") != field_name:
            continue
        filename = params.get("filename") or "upload.heic"
        return filename, part.get_payload(decode=True) or b""

    raise ValueError("No image uploaded")


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

    def do_GET(self):
        if self.path == "/app-config":
            self._send_json(
                {
                    "supabaseUrl": public_supabase_url(),
                    "supabaseAnonKey": os.environ.get("SUPABASE_ANON_KEY", "").strip(),
                    "foodLogTable": os.environ.get("SUPABASE_FOOD_LOG_TABLE", "food_logs").strip(),
                    "photoBucket": os.environ.get("SUPABASE_PHOTO_BUCKET", "food-photos").strip(),
                }
            )
            return
        super().do_GET()

    def do_POST(self):
        if self.path == "/convert-heic":
            self._convert_heic()
            return
        if self.path == "/nutrition-pipeline":
            self._nutrition_pipeline()
            return
        if self.path == "/manual-correction":
            self._manual_correction()
            return

        self.send_error(404, "Unknown endpoint")

    def _nutrition_pipeline(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            self._send_json(build_nutrition_result(payload))
        except Exception as error:
            self._send_json({"error": "Nutrition pipeline failed", "details": str(error)}, status=400)

    def _manual_correction(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            self._send_json(build_manual_correction_result(payload))
        except Exception as error:
            self._send_json({"error": "Manual correction failed", "details": str(error)}, status=400)

    def _convert_heic(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            filename, upload_bytes = parse_multipart_upload(self.headers, self.rfile.read(length))
        except Exception as error:
            self._send_json({"error": "No image uploaded"}, status=400)
            return

        suffix = Path(filename).suffix.lower() or ".heic"
        if suffix not in {".heic", ".heif"}:
            self._send_json({"error": "Only HEIC or HEIF files are converted here"}, status=400)
            return

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / f"upload{suffix}"
            output = Path(temp_dir) / "converted.jpg"

            source.write_bytes(upload_bytes)

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
