#!/usr/bin/env python3
import json
import mimetypes
import os
import re
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

FRUIT_VEG_TERMS = {
    "apple", "apricot", "asparagus", "avocado", "banana", "beet", "berries", "berry",
    "blueberry", "broccoli", "cabbage", "carrot", "cauliflower", "celery", "cherry",
    "cucumber", "eggplant", "grape", "greens", "kale", "kiwi", "lettuce", "mango",
    "melon", "mushroom", "orange", "peach", "pear", "pepper", "pineapple", "plum",
    "potato", "raspberry", "spinach", "squash", "strawberry", "tomato", "zucchini",
}

LESS_PREFERRED_USDA_TERMS = {
    "babyfood", "baby food", "dried", "dehydrated", "powder", "chips", "flour",
    "bread", "muffin", "cake", "pie", "pudding", "candy", "juice", "nectar",
    "beverage", "smoothie", "sweetened", "syrup", "branded",
}

PROTEIN_FOOD_TERMS = {
    "beef", "burger", "chicken", "cod", "cutlet", "egg", "fish", "lamb", "meat",
    "pork", "salmon", "shrimp", "steak", "tempeh", "tofu", "tuna", "turkey",
}

BASE_FOOD_TERMS = {
    "bagel", "bread", "cereal", "farro", "grain", "noodle", "oat", "oatmeal",
    "pasta", "porridge", "quinoa", "rice", "spaghetti",
}

DESSERT_FOOD_TERMS = {
    "brownie", "cake", "cookie", "cupcake", "donut", "doughnut", "ice cream",
    "pastry", "pie", "pudding", "tart",
}

PROCESSED_BASE_PRODUCT_TERMS = {
    "bar", "bread", "cracker", "crackers", "granola", "muffin", "muffins", "roll", "rolls", "snack",
}

PREPARED_DISH_TERMS = {
    "bake", "baked", "bowl", "burrito", "casserole", "curry", "enchilada",
    "lasagna", "parmesan", "parm", "sandwich", "stew", "stir fry", "taco",
}


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
        role = component.get("role") or infer_component_role(
            component["key"],
            component.get("label", ""),
            component.get("query", ""),
        )
        nutrient_role = component.get("nutrient_role") or infer_nutrient_role(
            component["key"],
            component.get("label", ""),
            component.get("query", ""),
        )
        component_food = lookup_nutrition(
            component.get("query") or component.get("label") or NUTRITION_DB[component["key"]]["name"],
            component["key"],
            role,
            nutrient_role,
            bool(payload.get("debugUsda")),
        )
        component_food["key"] = component["key"]
        component_food["name"] = component.get("label") or component_food["name"]
        component_food["serving"] = component.get("serving_estimate") or component_food["serving"]
        component_food["confidence"] = component.get("confidence", 0.5)
        component_food["portion"] = component.get("portion", 1)
        component_food["role"] = role
        component_food["nutrient_role"] = nutrient_role
        component_foods.append(component_food)

    food = combine_components(component_foods, vision.get("dish_name", ""))
    primary = ranked[0]
    key = primary["key"]
    food["key"] = key
    food["confidence"] = round(sum(item.get("confidence", 0) for item in component_foods) / max(1, len(component_foods)), 2)
    food["alternatives"] = correction_alternatives(vision, ranked, key, food.get("name", ""))
    food["pipeline"] = {
        "steps": [
            "File accepted",
            "HEIC/HEIF converted to JPEG" if converted else "Image decoded in browser",
            "OpenAI vision identified food candidates",
            "Server normalized candidates",
            "USDA FoodData Central lookup" if food.get("databaseSource") == "USDA FoodData Central" else "No reliable nutrition match",
            "FeedNomi score calculated",
        ],
        "sourceFormat": source_format,
        "converted": converted,
        "signals": signals,
        "visionProvider": "OpenAI Responses API",
        "nutritionProvider": food.get("databaseSource", "Nutrition unavailable"),
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
    component_food = lookup_nutrition(
        component["query"],
        component["key"],
        component["role"],
        component["nutrient_role"],
        bool(payload.get("debugUsda")),
    )
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
    food["loggable"] = not food.get("nutritionUnavailable")
    food["corrected"] = True
    food["correctionLabel"] = label
    food["pipeline"] = {
        "steps": [
            "User corrected food identity",
            "Server looked up corrected food",
            "USDA FoodData Central lookup" if food.get("databaseSource") == "USDA FoodData Central" else "No reliable nutrition match",
            "FeedNomi score recalculated" if not food.get("nutritionUnavailable") else "Nutrition unavailable; no score saved",
        ],
        "sourceFormat": "manual correction",
        "converted": False,
        "signals": {},
        "visionProvider": "Manual correction",
        "nutritionProvider": food.get("databaseSource", "Nutrition unavailable"),
        "notes": food.get("databaseNotes", []),
        "componentCount": 1,
    }
    return food


def build_selected_usda_result(payload):
    label = (payload.get("label") or payload.get("food") or "").strip()
    candidate = payload.get("candidate") or {}
    match = candidate.get("food") or candidate
    if not label or not match:
        return analysis_unavailable_result(
            "Choose a USDA nutrition row before applying it.",
            "manual USDA selection",
            False,
            {},
        )

    key = normalize_component_key(payload.get("key", "mixed"), label, label)
    base = dict(NUTRITION_DB.get(key, NUTRITION_DB["mixed"]))
    role = payload.get("role") or infer_component_role(key, label, label)
    nutrient_role = payload.get("nutrientRole") or payload.get("nutrient_role") or infer_nutrient_role(key, label, label)
    component_food = apply_usda_match(base, match, label, selected_by_user=True)
    component_food["key"] = key
    component_food["name"] = label
    component_food["confidence"] = 1
    component_food["portion"] = 1
    component_food["role"] = role
    component_food["nutrient_role"] = nutrient_role

    food = combine_components([component_food], label)
    food["key"] = key
    food["confidence"] = 1
    food["alternatives"] = []
    food["loggable"] = not food.get("nutritionUnavailable")
    food["corrected"] = True
    food["correctionLabel"] = label
    food["selectedUsda"] = {
        "fdcId": match.get("fdcId"),
        "description": match.get("description", label),
        "dataType": match.get("dataType", "USDA"),
    }
    food["pipeline"] = {
        "steps": [
            "User selected USDA nutrition row",
            "Server applied selected USDA nutrients",
            "FeedNomi score recalculated",
        ],
        "sourceFormat": "manual USDA selection",
        "converted": False,
        "signals": {},
        "visionProvider": "Manual correction",
        "nutritionProvider": food.get("databaseSource", "USDA FoodData Central"),
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
        '"portion":0.0,"confidence":0.0}],'
        '"dish_alternatives":[{"label":"complete alternate dish identity","canonical":"broccoli|chicken|berries|oatmeal|syrup|pasta|eggplant_parmesan|chicken_parmesan|pizza|cake|mixed",'
        '"query":"plain USDA food search query","confidence":0.0,"reason":"short visual reason"}],'
        '"dish_name":"string","notes":["short note"]}. '
        "If the image is not food, return is_food=false, confidence, foods=[], dish_name='', and notes explaining what was seen. "
        "Identify the complete dish and visible components, not only the most colorful ingredient. "
        "For example, oatmeal with blueberries and syrup should return dish_name='oatmeal with blueberries and syrup' "
        "and separate foods for oatmeal, blueberries, and syrup. Mark syrup, honey, butter, sauces, dressings, drizzles, and condiments as role='topping' or role='sauce'. Portion is the approximate share of the dish "
        "from 0 to 1. Prefer common food names that can be searched in USDA FoodData Central. "
        "For visually similar dishes, include complete dish alternatives with confidence scores in dish_alternatives, not as component foods. "
        "For example, a breaded cutlet with red sauce and cheese could include eggplant parmesan, chicken parmesan, and veal parmesan. "
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


def normalized_label(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def labels_refer_to_same_food(left, right):
    left_label = normalized_label(left)
    right_label = normalized_label(right)
    if not left_label or not right_label:
        return False
    if left_label == right_label:
        return True
    shorter, longer = sorted([left_label, right_label], key=len)
    return len(shorter) >= 8 and f" {shorter} " in f" {longer} "


def dish_alternatives_from_vision(vision, primary_key, current_name=""):
    raw_options = vision.get("dish_alternatives") or vision.get("alternatives") or []
    options = []
    seen = set()
    current_labels = [
        vision.get("dish_name", ""),
        current_name,
        NUTRITION_DB.get(primary_key, NUTRITION_DB["mixed"])["name"],
    ]

    for item in raw_options:
        if not isinstance(item, dict):
            continue
        label = (item.get("label") or item.get("query") or "").strip()
        query = (item.get("query") or label).strip()
        if not label and not query:
            continue

        key = normalize_component_key(item.get("canonical", "mixed"), label, query)
        name = label or query or NUTRITION_DB.get(key, NUTRITION_DB["mixed"])["name"]
        name_marker = normalized_label(name)
        if not name_marker or any(labels_refer_to_same_food(current, name) for current in current_labels):
            continue
        if key == primary_key and key != "mixed":
            continue

        try:
            confidence = float(item.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = round(max(0.05, min(0.96, confidence)), 2)

        marker = (key, name_marker)
        if marker in seen:
            continue
        seen.add(marker)
        options.append(
            {
                "key": key,
                "name": name,
                "confidence": confidence,
                "source": "Vision alternative",
            }
        )

    return options[:4]


def correction_alternatives(vision, ranked, primary_key, current_name=""):
    options = dish_alternatives_from_vision(vision, primary_key, current_name)
    if options:
        return options

    if vision.get("dish_name") and len(vision.get("foods") or []) > 1:
        return []

    for item in ranked[1:4]:
        item_name = item.get("label") or NUTRITION_DB[item["key"]]["name"]
        if (
            item.get("confidence", 0) <= 0.1
            or item["key"] == primary_key
            or item["key"] == "mixed"
            or labels_refer_to_same_food(current_name, item_name)
        ):
            continue
        options.append(
            {
                "key": item["key"],
                "name": item_name,
                "confidence": item["confidence"],
                "source": "Vision alternative",
            }
        )
    return options


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


def text_has_term(text, terms):
    words = (text or "").lower()
    return any(re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", words) for term in terms)


def infer_component_role(canonical, label="", query=""):
    text = f"{canonical} {label} {query}".lower()
    if any(word in text for word in ["syrup", "honey", "sugar", "sweetener", "jam", "jelly", "molasses"]):
        return "sweetener"
    if any(word in text for word in ["sauce", "dressing", "drizzle", "butter", "cream", "mayo", "aioli", "ranch", "glaze"]):
        return "topping"
    if text_has_term(text, PROTEIN_FOOD_TERMS):
        return "protein"
    if text_has_term(text, PREPARED_DISH_TERMS):
        return "prepared"
    if canonical in {"berries", "broccoli"} or text_has_term(text, FRUIT_VEG_TERMS):
        return "fruit_veg"
    if text_has_term(text, BASE_FOOD_TERMS):
        return "base"
    if text_has_term(text, DESSERT_FOOD_TERMS):
        return "dessert"
    return "mix_in"


def infer_nutrient_role(canonical, label="", query=""):
    text = f"{canonical} {label} {query}".lower()
    if any(word in text for word in ["syrup", "honey", "sugar", "jam", "jelly"]):
        return "added_sugar"
    if text_has_term(text, PROTEIN_FOOD_TERMS):
        return "protein"
    if canonical in {"berries", "broccoli", "oatmeal"} or text_has_term(text, FRUIT_VEG_TERMS):
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

    unavailable_components = [food for food in component_foods if food.get("nutritionUnavailable")]
    if unavailable_components:
        notes = []
        source_matches = []
        for food in component_foods:
            notes.extend(food.get("databaseNotes", []))
            source_matches.append(f"{food.get('name', 'Food')}: {food.get('sourceMatch', 'No reliable USDA match')}")
        return {
            "name": name,
            "serving": " + ".join(food.get("serving", "estimated serving") for food in component_foods),
            "nutritionBasis": "No reliable nutrition source",
            "calories": "-",
            "protein": "-",
            "fiber": "-",
            "sugar": "-",
            "naturalSugar": 0,
            "addedSugar": 0,
            "unknownSugar": 0,
            "why": "Nutrition could not be calculated because at least one component did not have a reliable USDA match.",
            "databaseSource": "Nutrition unavailable",
            "databaseNotes": dedupe(notes)[:5],
            "sourceMatch": "; ".join(source_matches[:3]),
            "portionConfidence": "Low - nutrition source unavailable",
            "components": [
                {
                    "name": food["name"],
                    "serving": food.get("serving", "estimated serving"),
                    "nutritionBasis": food.get("nutritionBasis", "No reliable nutrition source"),
                    "sourceMatch": food.get("sourceMatch", "No reliable USDA match"),
                    "role": food.get("role", "mix_in"),
                    "nutrientRole": food.get("nutrient_role", "neutral"),
                    "portion": food.get("portion", 1),
                    "calories": "-",
                    "confidence": food.get("confidence", 0.5),
                    "why": "no reliable USDA nutrition source",
                }
                for food in component_foods
            ],
            "points": 0,
            "effect": "Avatar unchanged",
            "loggable": False,
            "nutritionUnavailable": True,
            "usdaCandidates": unavailable_components[0].get("usdaCandidates", []),
        }

    weighted_components = [with_portion_weight(food) for food in component_foods]
    calories = sum(food["weightedCalories"] for food in weighted_components)
    protein = sum(food["weightedProtein"] for food in weighted_components)
    fiber = sum(food["weightedFiber"] for food in weighted_components)
    sugar = sum(food["weightedSugar"] for food in weighted_components)
    natural_sugar = sum(food["weightedNaturalSugar"] for food in weighted_components)
    added_sugar = sum(food["weightedAddedSugar"] for food in weighted_components)
    unknown_sugar = sum(food["weightedUnknownSugar"] for food in weighted_components)
    db_sources = sorted({food.get("databaseSource", "Local nutrition fallback") for food in component_foods})
    source_matches = [
        f"{food.get('name', 'Food')}: {food.get('sourceMatch', food.get('databaseSource', 'Local estimate'))}"
        for food in component_foods
    ]
    avg_confidence = sum(float(food.get("confidence", 0.5) or 0.5) for food in component_foods) / max(1, len(component_foods))
    notes = []
    for food in component_foods:
        notes.extend(food.get("databaseNotes", []))

    combined = {
        "name": name,
        "serving": " + ".join(food.get("serving", "estimated serving") for food in component_foods),
        "nutritionBasis": " + ".join(food.get("nutritionBasis", food.get("serving", "estimated serving")) for food in component_foods),
        "calories": round(calories),
        "protein": f"{protein:.0f} g",
        "fiber": f"{fiber:.0f} g",
        "sugar": f"{sugar:.0f} g",
        "naturalSugar": round(natural_sugar, 1),
        "addedSugar": round(added_sugar, 1),
        "unknownSugar": round(unknown_sugar, 1),
        "why": build_component_why(component_foods),
        "databaseSource": " + ".join(db_sources),
        "databaseNotes": dedupe(notes)[:5],
        "sourceMatch": "; ".join(source_matches[:3]),
        "portionConfidence": portion_confidence_label(avg_confidence, len(component_foods)),
        "components": [
            {
                "name": food["name"],
                "serving": food.get("serving", "estimated serving"),
                "nutritionBasis": food.get("nutritionBasis", food.get("serving", "estimated serving")),
                "sourceMatch": food.get("sourceMatch", food.get("databaseSource", "Local estimate")),
                "role": food.get("role", "mix_in"),
                "nutrientRole": food.get("nutrient_role", "neutral"),
                "portion": food["portionWeight"],
                "calories": round(food["weightedCalories"]),
                "confidence": food.get("confidence", 0.5),
                "why": short_component_why(food),
            }
            for food in weighted_components
        ],
    }
    debug_candidates = next((food.get("usdaCandidates") for food in component_foods if food.get("usdaCandidates")), None)
    if debug_candidates:
        combined["usdaCandidates"] = debug_candidates
        combined["debugUsda"] = True
    combined["points"] = calculate_points(combined)
    combined["effect"] = effect_for_points(combined["points"])
    return combined


def portion_confidence_label(avg_confidence, component_count):
    if component_count > 3:
        return "Low - complex mixed meal"
    if avg_confidence >= 0.82:
        return "Medium - clear food, visual serving estimate"
    if avg_confidence >= 0.58:
        return "Low-medium - visual serving estimate"
    return "Low - adjust serving size when available"


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
        "fruit_veg": 1.0,
        "base": 1.0,
        "protein": 1.0,
        "dessert": 1.0,
        "prepared": 1.0,
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
        "prepared": 0.2,
    }
    portion = min(portion, role_caps.get(role, 1.0))
    portion = max(role_floors.get(role, 0.1), min(1.0, portion))

    weighted["role"] = role
    weighted["portionWeight"] = portion
    weighted["weightedCalories"] = float(food.get("calories", 0)) * portion
    weighted["weightedProtein"] = parse_grams(food.get("protein", "0 g")) * portion
    weighted["weightedFiber"] = parse_grams(food.get("fiber", "0 g")) * portion
    weighted["weightedSugar"] = parse_grams(food.get("sugar", "0 g")) * portion
    weighted["weightedNaturalSugar"] = 0
    weighted["weightedAddedSugar"] = 0
    weighted["weightedUnknownSugar"] = weighted["weightedSugar"]
    if role == "fruit_veg" and food.get("nutrient_role") != "added_sugar":
        weighted["weightedNaturalSugar"] = weighted["weightedSugar"]
        weighted["weightedUnknownSugar"] = 0
    if role in {"sweetener", "condiment", "sauce", "topping", "dessert"} or food.get("nutrient_role") == "added_sugar":
        weighted["weightedAddedSugar"] = weighted["weightedSugar"]
        weighted["weightedUnknownSugar"] = 0
    if role in {"sweetener", "condiment", "sauce", "topping"}:
        weighted["weightedCalories"] = min(weighted["weightedCalories"], 70)
        weighted["weightedSugar"] = min(weighted["weightedSugar"], 14)
        weighted["weightedAddedSugar"] = min(weighted["weightedAddedSugar"], 14)
    if role == "sweetener":
        weighted["weightedCalories"] = min(weighted["weightedCalories"], 60)
        weighted["weightedSugar"] = min(weighted["weightedSugar"], 12)
        weighted["weightedAddedSugar"] = min(weighted["weightedAddedSugar"], 12)
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
        added_sugar = float(food.get("addedSugar", food.get("weightedAddedSugar", 0)) or 0)
    except (TypeError, ValueError):
        added_sugar = 0
    try:
        natural_sugar = float(food.get("naturalSugar", food.get("weightedNaturalSugar", 0)) or 0)
    except (TypeError, ValueError):
        natural_sugar = 0
    try:
        calories = float(food.get("calories", 0))
    except (TypeError, ValueError):
        calories = 0

    reasons = []
    if protein >= 15:
        reasons.append("meaningful protein for fullness")
    if fiber >= 4:
        reasons.append("fiber that supports satiety and steadier energy")
    if added_sugar >= 8:
        reasons.append("added sugar, so portion size matters")
    elif sugar >= 10 and natural_sugar >= sugar * 0.6:
        reasons.append("natural sweetness with no meaningful added sugar")
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
        why = short_component_why(food)
        verb = "contribute" if food["name"].lower().endswith("s") else "contributes"
        if why.startswith(("contributes ", "adds ", "provides ")):
            return f"{food['name']} {why}."
        return f"{food['name']} {verb} {why}."

    sentences = []
    for food in component_foods[:4]:
        why = short_component_why(food)
        verb = "contribute" if food["name"].lower().endswith("s") else "contributes"
        if why.startswith(("contributes ", "adds ", "provides ")):
            sentences.append(f"{food['name']} {why}.")
        else:
            sentences.append(f"{food['name']} {verb} {why}.")
    sentences.append("Together, these choices determine the overall FeedNomi score.")
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


def nutrition_unavailable_component(base, notes, query=""):
    return {
        "name": base.get("name") or query or "Food",
        "serving": base.get("serving", "estimated serving"),
        "nutritionBasis": "No reliable nutrition source",
        "calories": 0,
        "protein": "0 g",
        "fiber": "0 g",
        "sugar": "0 g",
        "naturalSugar": 0,
        "addedSugar": 0,
        "unknownSugar": 0,
        "points": 0,
        "why": "Nutrition could not be calculated because no reliable database match was found.",
        "effect": "Avatar unchanged",
        "databaseSource": "Nutrition unavailable",
        "databaseNotes": notes,
        "sourceMatch": "No reliable USDA match",
        "nutritionUnavailable": True,
        "loggable": False,
        "usdaCandidates": [],
    }


def usda_candidate_options(ranked_matches):
    options = []
    for item in ranked_matches:
        food = item.get("food", {})
        nutrients = item.get("nutrients") or extract_usda_nutrients(food.get("foodNutrients", []))
        slim_food = {
            "fdcId": food.get("fdcId"),
            "description": food.get("description", ""),
            "dataType": food.get("dataType", "USDA"),
            "brandOwner": food.get("brandOwner", ""),
            "servingSize": food.get("servingSize"),
            "servingSizeUnit": food.get("servingSizeUnit", ""),
            "foodNutrients": [
                {"nutrientName": "Energy", "value": nutrients.get("calories")},
                {"nutrientName": "Protein", "value": nutrients.get("protein")},
                {"nutrientName": "Fiber, total dietary", "value": nutrients.get("fiber")},
                {"nutrientName": "Total Sugars", "value": nutrients.get("added_sugar") or nutrients.get("sugar")},
            ],
        }
        options.append(
            {
                "fdcId": food.get("fdcId"),
                "description": food.get("description", ""),
                "dataType": food.get("dataType", "USDA"),
                "brandOwner": food.get("brandOwner", ""),
                "servingSize": food.get("servingSize"),
                "servingSizeUnit": food.get("servingSizeUnit", ""),
                "calories": nutrients.get("calories"),
                "protein": nutrients.get("protein"),
                "fiber": nutrients.get("fiber"),
                "sugar": nutrients.get("added_sugar") or nutrients.get("sugar"),
                "rejectedReason": item.get("rejectedReason", ""),
                "validatorDecision": item.get("validatorDecision"),
                "food": slim_food,
            }
        )
    return options


def apply_usda_match(base, match, query, selected_by_user=False):
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
    usda_serving = match.get("servingSizeUnit") and match.get("servingSize") and (
        f"{match.get('servingSize')} {match.get('servingSizeUnit')}"
    )
    base["serving"] = usda_serving or base["serving"]
    base["nutritionBasis"] = usda_serving or "USDA database amount (serving not specified)"
    base["databaseSource"] = "USDA FoodData Central"
    base["sourceMatch"] = f"{match.get('description', query)} ({match.get('dataType', 'USDA')}, FDC {match.get('fdcId', 'unknown')})"
    base["databaseNotes"] = [
        f"Matched FDC ID {match.get('fdcId')} ({match.get('dataType', 'unknown type')}).",
        "USDA values may be per serving or per 100 g depending on record metadata.",
    ]
    if selected_by_user:
        base["databaseNotes"].append("User selected this USDA nutrition row.")
    base["points"] = calculate_points(base)
    base["effect"] = effect_for_points(base["points"])
    base["nutritionUnavailable"] = False
    base["loggable"] = True
    return base


def lookup_nutrition(query, fallback_key, role="", nutrient_role="", include_candidates=False):
    base = dict(NUTRITION_DB.get(fallback_key, NUTRITION_DB["mixed"]))
    api_key = os.environ.get("USDA_API_KEY", "").strip()
    if not api_key:
        return nutrition_unavailable_component(
            base,
            ["Set USDA_API_KEY to enable FoodData Central lookup. No local nutrition estimate was used."],
            query,
        )

    try:
        search_url = f"{USDA_SEARCH_URL}?{urllib.parse.urlencode({'api_key': api_key})}"
        result = post_json(
            search_url,
            {
                "query": query,
                "pageSize": 12,
                "pageNumber": 1,
                "dataType": ["Foundation", "SR Legacy", "Survey (FNDDS)", "Branded"],
            },
            timeout=20,
        )
        foods = result.get("foods") or []
        if not foods:
            raise ValueError("No USDA matches")
        ranked_matches = rank_usda_matches(query, foods, base, role, nutrient_role)
        ranked_matches = apply_openai_usda_validation(query, ranked_matches, base, role, nutrient_role)
        accepted = next((item for item in ranked_matches if not item["rejectedReason"]), None)
        best_candidate = accepted or ranked_matches[0]
        match = best_candidate["food"]
        rejected_matches = [item for item in ranked_matches if item["rejectedReason"]]
        validator_count = sum(1 for item in ranked_matches if item.get("validatorDecision"))
        nutrients = extract_usda_nutrients(match.get("foodNutrients", []))
        calories = nutrients.get("calories")
        protein = nutrients.get("protein")
        fiber = nutrients.get("fiber")
        sugar = nutrients.get("added_sugar") or nutrients.get("sugar")
        rejected_reason = best_candidate["rejectedReason"]
        if rejected_reason:
            notes = []
            if validator_count:
                notes.append(f"OpenAI match validator reviewed {validator_count} USDA candidates.")
            notes.extend(
                [
                    rejected_reason,
                    f"Rejected USDA match FDC ID {match.get('fdcId')} ({match.get('description', query)}).",
                ]
            )
            notes.append("No local nutrition estimate was used.")
            unavailable = nutrition_unavailable_component(base, notes, query)
            unavailable["usdaCandidates"] = usda_candidate_options(ranked_matches[:8])
            return unavailable
        base = apply_usda_match(base, match, query)
        if include_candidates:
            base["usdaCandidates"] = usda_candidate_options(ranked_matches[:8])
            base["debugUsda"] = True
        if validator_count:
            base["databaseNotes"].append(f"OpenAI match validator reviewed {validator_count} USDA candidates.")
        if rejected_matches:
            base["databaseNotes"].append(
                f"Skipped {len(rejected_matches)} lower-quality USDA match{'es' if len(rejected_matches) != 1 else ''}."
            )
        base["points"] = calculate_points(base)
        base["effect"] = effect_for_points(base["points"])
        return base
    except Exception as error:
        return nutrition_unavailable_component(
            base,
            [f"USDA lookup failed: {error}", "No local nutrition estimate was used."],
            query,
        )


def rejected_usda_match_reason(fallback_key, match, nutrients, fallback, role="", nutrient_role="", query=""):
    description = (match.get("description") or "").lower()
    query_text = (query or "").lower()
    serving_unit = str(match.get("servingSizeUnit") or "").lower()
    protein = nutrients.get("protein")
    calories = nutrients.get("calories")
    fiber = nutrients.get("fiber")
    sugar = nutrients.get("added_sugar") or nutrients.get("sugar")
    fallback_protein = parse_grams(fallback.get("protein", "0 g"))
    fallback_fiber = parse_grams(fallback.get("fiber", "0 g"))
    fallback_calories = float(fallback.get("calories", 0) or 0)

    if serving_unit and serving_unit in {"iu", "mcg", "mg"} and role in {"base", "prepared", "protein", "fruit_veg"}:
        return "USDA match had unusable serving-size metadata for this food, so nutrition was not calculated."

    if role == "protein":
        query_protein_terms = {term for term in PROTEIN_FOOD_TERMS if term in query_text}
        if query_protein_terms and not any(term in description for term in query_protein_terms):
            return "USDA match did not include the protein named in the food, so nutrition was not calculated."
        protein_floor = 12
        if fallback_protein >= 18:
            protein_floor = max(protein_floor, min(32, fallback_protein * 0.6))
        if protein is None:
            return "USDA match was missing protein data for a protein-forward food, so nutrition was not calculated."
        if protein < protein_floor:
            return (
                f"USDA match protein was {protein:.0f} g, below the expected range for a protein-forward food; "
                "nutrition was not calculated."
            )

    if role == "fruit_veg" and any(term in description for term in ["juice", "nectar", "syrup", "pie filling", "candied"]):
        return "USDA match looked like a sweetened or processed produce item, so the cleaner food estimate was kept."

    if role == "prepared":
        query_prepared_terms = {term for term in PREPARED_DISH_TERMS if text_has_term(query_text, {term})}
        if query_prepared_terms and not any(term in description for term in query_prepared_terms):
            return "USDA match looked like a plain ingredient rather than the prepared dish, so nutrition was not calculated."
        if fallback_calories >= 150 and calories is not None and calories < fallback_calories * 0.55:
            return "USDA match calories were too low for the prepared dish profile, so nutrition was not calculated."
        if fallback_protein >= 10 and protein is not None and protein < fallback_protein * 0.5:
            return "USDA match protein was too low for the prepared dish profile, so nutrition was not calculated."
        if fallback_fiber >= 5 and fiber is not None and fiber < fallback_fiber * 0.45:
            return "USDA match fiber was too low for the prepared dish profile, so nutrition was not calculated."

    if role == "sweetener" and sugar is not None and sugar < 4 and calories is not None and calories < 20:
        return "USDA match did not look like a meaningful sweetener portion, so nutrition was not calculated."

    if role == "base" and calories is not None and calories < 40:
        return "USDA match looked too low-calorie for a grain or starch base, so nutrition was not calculated."

    if role == "base" and fallback_calories >= 100 and calories is not None and calories < fallback_calories * 0.6:
        return "USDA match calories were too low for the stated grain or starch serving, so nutrition was not calculated."

    if role == "base" and fallback_calories >= 100 and calories is not None and calories > fallback_calories * 2.5:
        return "USDA match calories were too high for the stated grain or starch serving, so nutrition was not calculated."

    if role == "base" and sugar is not None and sugar > 12 and not text_has_term(query_text, {"sweet", "sweetened", "flavored", "maple", "brown sugar"}):
        return "USDA match looked like a sweetened grain product rather than a plain base, so nutrition was not calculated."

    if role == "base" and text_has_term(description, DESSERT_FOOD_TERMS) and not text_has_term(query_text, DESSERT_FOOD_TERMS):
        return "USDA match looked like a dessert rather than a grain or starch base, so nutrition was not calculated."

    if role == "base" and text_has_term(description, PROCESSED_BASE_PRODUCT_TERMS) and not text_has_term(query_text, PROCESSED_BASE_PRODUCT_TERMS):
        return "USDA match looked like a processed snack product rather than a grain or starch base, so nutrition was not calculated."

    if fallback_protein >= 20 and protein is not None and protein < fallback_protein * 0.6:
        return (
            "USDA match was much lower protein than the expected dish profile, so nutrition was not calculated."
        )

    if calories is None and protein is None and fiber is None and sugar is None:
        return "USDA match did not include usable calories or macro data, so nutrition was not calculated."

    return ""


def best_usda_match(query, foods):
    ranked = rank_usda_matches(query, foods, {}, "", "")
    return ranked[0]["food"] if ranked else foods[0]


def rank_usda_matches(query, foods, fallback, role="", nutrient_role=""):
    query_terms = {term for term in re.findall(r"[a-z]+", (query or "").lower()) if len(term) > 2}
    data_type_score = {
        "Foundation": 40,
        "SR Legacy": 35,
        "Survey (FNDDS)": 28,
        "Branded": 4,
    }

    def score(food, nutrients):
        description = (food.get("description") or "").lower()
        data_type = food.get("dataType", "")
        words = set(re.findall(r"[a-z]+", description))
        value = data_type_score.get(data_type, 10)
        value += sum(8 for term in query_terms if term in words or term in description)
        if any(term in description for term in [" raw", ", raw", "fresh", "cooked", "boiled", "steamed"]):
            value += 10
        if any(term in description for term in LESS_PREFERRED_USDA_TERMS):
            value -= 18
        if data_type == "Branded":
            value -= 10
        value += nutrition_plausibility_score(description, nutrients, fallback, role, nutrient_role, query)
        return value

    ranked = []
    for food in foods:
        nutrients = extract_usda_nutrients(food.get("foodNutrients", []))
        ranked.append(
            {
                "food": food,
                "nutrients": nutrients,
                "score": score(food, nutrients),
                "rejectedReason": rejected_usda_match_reason(
                    "",
                    food,
                    nutrients,
                    fallback,
                    role,
                    nutrient_role,
                    query,
                ),
            }
        )
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked


def apply_openai_usda_validation(query, ranked_matches, fallback, role="", nutrient_role=""):
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key or not ranked_matches:
        return ranked_matches

    candidates = []
    for item in ranked_matches[:8]:
        food = item["food"]
        nutrients = item.get("nutrients") or {}
        candidates.append(
            {
                "fdcId": food.get("fdcId"),
                "description": food.get("description"),
                "dataType": food.get("dataType"),
                "brandOwner": food.get("brandOwner"),
                "servingSize": food.get("servingSize"),
                "servingSizeUnit": food.get("servingSizeUnit"),
                "calories": nutrients.get("calories"),
                "protein": nutrients.get("protein"),
                "fiber": nutrients.get("fiber"),
                "sugar": nutrients.get("added_sugar") or nutrients.get("sugar"),
            }
        )

    prompt = (
        "You validate USDA FoodData Central search results for a nutrition logging app. "
        "Decide whether each candidate is the same food the user requested and compatible with the stated serving style. "
        "Reject candidates that are only an ingredient of the dish, a different dish, a bakery/snack/sweetened/branded variant when the request is plain, "
        "or have serving metadata that makes the nutrition basis unreliable for the requested food. "
        "Accept a branded row only when its description clearly represents the requested food. "
        "Return JSON only with this exact shape: "
        '{"candidates":[{"fdcId":123,"accept":true,"confidence":0.0,"reason":"short reason"}]}. '
        "Keep reasons short and specific."
    )
    payload = {
        "model": os.environ.get("OPENAI_VALIDATOR_MODEL", os.environ.get("OPENAI_VISION_MODEL", "gpt-4.1-mini")),
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(
                            {
                                "instructions": prompt,
                                "requestedFood": query,
                                "role": role,
                                "nutrientRole": nutrient_role,
                                "localFallback": {
                                    "name": fallback.get("name"),
                                    "serving": fallback.get("serving"),
                                    "calories": fallback.get("calories"),
                                    "protein": fallback.get("protein"),
                                    "fiber": fallback.get("fiber"),
                                    "sugar": fallback.get("sugar"),
                                },
                                "candidates": candidates,
                            }
                        ),
                    }
                ],
            }
        ],
    }

    try:
        response = post_json(
            OPENAI_RESPONSES_URL,
            payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=25,
        )
        validation = json.loads(extract_response_text(response))
    except Exception:
        return ranked_matches

    decisions = {}
    for decision in validation.get("candidates", []):
        fdc_id = decision.get("fdcId")
        if fdc_id is None:
            continue
        decisions[str(fdc_id)] = decision

    validated = []
    for item in ranked_matches:
        food = item["food"]
        decision = decisions.get(str(food.get("fdcId")))
        next_item = dict(item)
        if decision:
            accept = bool(decision.get("accept"))
            confidence = max(0, min(1, float(decision.get("confidence", 0) or 0)))
            reason = str(decision.get("reason") or "OpenAI validator rejected this USDA match.").strip()
            next_item["validatorDecision"] = {
                "accept": accept,
                "confidence": confidence,
                "reason": reason,
            }
            if accept:
                next_item["score"] += int(round(confidence * 20))
            elif not next_item.get("rejectedReason"):
                next_item["rejectedReason"] = f"OpenAI match validator rejected this USDA row: {reason}"
        validated.append(next_item)

    validated.sort(key=lambda item: item["score"], reverse=True)
    return validated


def nutrition_plausibility_score(description, nutrients, fallback, role="", nutrient_role="", query=""):
    score = 0
    query_text = (query or "").lower()
    protein = nutrients.get("protein")
    calories = nutrients.get("calories")
    fiber = nutrients.get("fiber")
    sugar = nutrients.get("added_sugar") or nutrients.get("sugar")
    fallback_protein = parse_grams(fallback.get("protein", "0 g"))
    fallback_fiber = parse_grams(fallback.get("fiber", "0 g"))
    fallback_calories = float(fallback.get("calories", 0) or 0)

    if role == "protein":
        query_protein_terms = {term for term in PROTEIN_FOOD_TERMS if term in query_text}
        if query_protein_terms and any(term in description for term in query_protein_terms):
            score += 18
        elif query_protein_terms:
            score -= 42
        if protein is None:
            score -= 30
        elif protein >= max(12, min(32, fallback_protein * 0.6 if fallback_protein else 12)):
            score += 24
        elif protein < 8:
            score -= 30
        else:
            score -= 10
    elif role == "fruit_veg":
        if fiber is not None and fiber >= 2:
            score += 12
        if sugar is not None and "added" not in description:
            score += 4
        if any(term in description for term in ["juice", "nectar", "syrup", "candied"]):
            score -= 28
    elif role == "sweetener":
        if sugar is not None and sugar >= 4:
            score += 18
        if any(term in description for term in ["syrup", "honey", "sugar", "molasses"]):
            score += 10
    elif role == "base":
        if text_has_term(description, DESSERT_FOOD_TERMS) and not text_has_term(query_text, DESSERT_FOOD_TERMS):
            score -= 45
        if text_has_term(description, PROCESSED_BASE_PRODUCT_TERMS) and not text_has_term(query_text, PROCESSED_BASE_PRODUCT_TERMS):
            score -= 45
        if fallback_calories >= 100 and calories is not None and calories < fallback_calories * 0.6:
            score -= 25
        if fallback_calories >= 100 and calories is not None and calories > fallback_calories * 2.5:
            score -= 25
        if sugar is not None and sugar > 12 and not text_has_term(query_text, {"sweet", "sweetened", "flavored", "maple", "brown sugar"}):
            score -= 30
        if calories is not None and calories >= 80:
            score += 10
        if protein is not None and protein > 25:
            score -= 10
    elif role == "prepared":
        query_prepared_terms = {term for term in PREPARED_DISH_TERMS if text_has_term(query_text, {term})}
        if query_prepared_terms and any(term in description for term in query_prepared_terms):
            score += 22
        elif query_prepared_terms:
            score -= 36
        if fallback_calories >= 150 and calories is not None:
            if calories >= fallback_calories * 0.55:
                score += 10
            else:
                score -= 18
        if fallback_protein >= 10 and protein is not None:
            if protein >= fallback_protein * 0.5:
                score += 8
            else:
                score -= 14
        if fallback_fiber >= 5 and fiber is not None:
            if fiber >= fallback_fiber * 0.45:
                score += 6
            else:
                score -= 10
    elif role == "dessert":
        if sugar is not None and sugar >= 10:
            score += 8

    if nutrient_role == "protein" and protein is not None and protein >= 12:
        score += 8
    if nutrient_role == "fiber" and fiber is not None and fiber >= 2:
        score += 8
    if nutrient_role == "added_sugar" and sugar is not None and sugar >= 4:
        score += 8

    return score


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
    components = food.get("components", [])
    produce_portion = sum(
        float(component.get("portion", 0) or 0)
        for component in components
        if component.get("role") == "fruit_veg"
    )
    has_produce = produce_portion > 0 or food.get("role") == "fruit_veg"
    natural_sugar = float(food.get("naturalSugar", sugar if has_produce else 0) or 0)
    added_sugar = float(food.get("addedSugar", 0) or 0)
    unknown_sugar = float(food.get("unknownSugar", max(0, sugar - natural_sugar - added_sugar)) or 0)
    sugar_penalty = added_sugar * 0.8 + unknown_sugar * 0.45 + natural_sugar * 0.15
    produce_bonus = min(10, 6 + produce_portion * 6) if has_produce else 0
    score = 18 + protein * 0.4 + fiber * 3 + produce_bonus - sugar_penalty - max(0, calories - 350) * 0.025
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


class FeedNomiHandler(SimpleHTTPRequestHandler):
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
        if self.path == "/select-usda":
            self._select_usda()
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

    def _select_usda(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            self._send_json(build_selected_usda_result(payload))
        except Exception as error:
            self._send_json({"error": "USDA selection failed", "details": str(error)}, status=400)

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
    server = ThreadingHTTPServer((host, port), FeedNomiHandler)
    print(f"FeedNomi running at http://{host}:{port}")
    server.serve_forever()
