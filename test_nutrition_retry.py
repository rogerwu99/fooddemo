import io
import json
import unittest
from unittest.mock import patch

import server


def component_food(key, portion, confidence=0.8):
    base = dict(server.NUTRITION_DB[key])
    food = server.local_fallback_component(base, base["name"])
    food.update({
        "key": key,
        "name": base["name"],
        "role": server.infer_component_role(key, base["name"], base["serving"]),
        "nutrient_role": server.infer_nutrient_role(key, base["name"], base["serving"]),
        "portion": portion,
        "estimatedShare": portion,
        "confidence": confidence,
    })
    return food


def unavailable_component(key, portion, confidence=0.7):
    base = dict(server.NUTRITION_DB[key])
    food = server.nutrition_unavailable_component(base, ["Simulated temporary lookup failure."], base["name"])
    food.update({
        "key": key,
        "name": base["name"],
        "role": server.infer_component_role(key, base["name"], base["serving"]),
        "nutrient_role": server.infer_nutrient_role(key, base["name"], base["serving"]),
        "portion": portion,
        "estimatedShare": portion,
        "confidence": confidence,
        "nutritionUnavailable": True,
    })
    return food


class ComponentNutritionRetryTests(unittest.TestCase):
    def test_recent_meal_hints_are_bounded_and_normalized(self):
        hints = server.sanitize_recent_meal_hints([
            {"name": "  Eggplant   Parmesan ", "frequency": 99, "components": ["Eggplant", "Eggplant", "  Tomato sauce  "]},
            {"name": "Tofu stir fry", "frequency": "bad", "components": ["tofu"]},
            {"name": ""},
            None,
        ])

        self.assertEqual(hints, [
            {"name": "Eggplant Parmesan", "frequency": 30, "components": ["Eggplant", "Tomato sauce"]},
            {"name": "Tofu stir fry", "frequency": 1, "components": ["tofu"]},
        ])
        self.assertEqual(len(server.sanitize_recent_meal_hints([{"name": str(i)} for i in range(10)])), 5)

    def test_vision_prompt_uses_history_only_as_soft_prior(self):
        response = {"output_text": '{"is_food":true,"foods":[],"dish_name":"Eggplant parmesan"}'}
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            with patch.object(server, "post_json", return_value=response) as post:
                server.analyze_with_openai_vision(
                    "data:image/jpeg;base64,test",
                    "meal.jpg",
                    [{"name": "Eggplant parmesan", "frequency": 5, "components": ["eggplant"]}],
                )

        prompt = post.call_args.args[1]["input"][0]["content"][0]["text"]
        self.assertIn('"frequency":5', prompt)
        self.assertIn("soft prior", prompt)
        self.assertIn("Never let history override clear visual evidence", prompt)

    def test_retry_reuses_known_components_and_queries_only_failed_piece(self):
        vision = {
            "dish_name": "Oatmeal with milk",
            "confidence": 0.9,
            "foods": [
                {
                    "canonical": "oatmeal",
                    "label": "Oatmeal",
                    "query": "cooked oatmeal",
                    "serving_estimate": "1 cup cooked",
                    "portion": 0.7,
                    "confidence": 0.9,
                },
                {
                    "canonical": "milk",
                    "label": "Milk",
                    "query": "milk",
                    "serving_estimate": "1/2 cup",
                    "portion": 0.3,
                    "confidence": 0.8,
                },
            ],
        }
        component_inputs = [
            {
                "key": "oatmeal", "label": "Oatmeal", "query": "cooked oatmeal",
                "serving_estimate": "1 cup cooked", "role": "base",
                "nutrient_role": "fiber", "portion": 0.7, "confidence": 0.9,
            },
            {
                "key": "milk", "label": "Milk", "query": "milk",
                "serving_estimate": "1/2 cup", "role": "protein",
                "nutrient_role": "protein", "portion": 0.3, "confidence": 0.8,
            },
        ]

        with patch.object(server, "analyze_with_openai_vision", return_value=vision) as vision_call:
            with patch.object(
                server,
                "lookup_component_nutrition_parallel",
                return_value=[component_food("oatmeal", 0.7), unavailable_component("milk", 0.3)],
            ) as initial_lookup:
                partial = server.build_nutrition_result({"imageDataUrl": "data:image/jpeg;base64,test"})

        self.assertEqual(partial["nutritionStatus"], "partial")
        self.assertEqual(partial["points"], 0)
        self.assertEqual(len(partial["componentNutritionInputs"]), 2)
        self.assertEqual(vision_call.call_count, 1)
        self.assertEqual(len(initial_lookup.call_args.args[0]), 2)

        with patch.object(server, "analyze_with_openai_vision", side_effect=AssertionError("vision must not rerun")):
            with patch.object(
                server,
                "lookup_component_nutrition_parallel",
                return_value=[component_food("milk", 0.3)],
            ) as retry_lookup:
                completed = server.build_nutrition_from_saved_components({
                    "dishName": partial["name"],
                    "primaryKey": partial["key"],
                    "componentInputs": partial["componentInputs"],
                    "componentNutritionInputs": partial["componentNutritionInputs"],
                })

        retried = retry_lookup.call_args.args[0]
        self.assertEqual(len(retried), 1)
        self.assertEqual(retried[0]["key"], "milk")
        self.assertEqual(completed["nutritionStatus"], "complete")
        self.assertGreater(completed["points"], 0)
        self.assertEqual(len(completed["components"]), 2)
        self.assertTrue(all(not item.get("nutritionUnavailable") for item in completed["componentNutritionInputs"]))

    def test_endpoint_flow_persists_partial_then_completes_same_row(self):
        vision = {
            "dish_name": "Oatmeal with milk",
            "confidence": 0.9,
            "foods": [
                {"canonical": "oatmeal", "label": "Oatmeal", "query": "cooked oatmeal", "serving_estimate": "1 cup cooked", "portion": 0.7, "confidence": 0.9},
                {"canonical": "milk", "label": "Milk", "query": "milk", "serving_estimate": "1/2 cup", "portion": 0.3, "confidence": 0.8},
            ],
        }

        def call_handler(method_name, payload):
            raw_body = json.dumps(payload).encode("utf-8")
            handler = object.__new__(server.FeedNomiHandler)
            handler.headers = {"Content-Length": str(len(raw_body))}
            handler.rfile = io.BytesIO(raw_body)
            handler.response = None
            handler._send_json = lambda body, status=200: setattr(handler, "response", (status, body))
            getattr(handler, method_name)()
            status, body = handler.response
            if status != 200:
                raise AssertionError(f"{method_name} returned {status}: {body}")
            return body

        row = {"id": "fixture-row", "name": "Meal photo", "nutrition": {}}
        with patch.object(server, "analyze_with_openai_vision", return_value=vision) as vision_call:
            with patch.object(
                server,
                "lookup_component_nutrition_parallel",
                return_value=[component_food("oatmeal", 0.7), unavailable_component("milk", 0.3)],
            ):
                partial = call_handler("_nutrition_pipeline", {"imageDataUrl": "data:image/jpeg;base64,test"})

        self.assertEqual(partial["nutritionStatus"], "partial")
        row["name"] = partial["name"]
        row["nutrition"] = {
            "processingStatus": "nutrition_pending",
            "nutritionStatus": partial["nutritionStatus"],
            "dishName": partial["name"],
            "primaryKey": partial["key"],
            "componentInputs": partial["componentInputs"],
            "componentNutritionInputs": partial["componentNutritionInputs"],
        }
        row_id = row["id"]

        with patch.object(server, "analyze_with_openai_vision", side_effect=AssertionError("vision must not rerun")):
            with patch.object(
                server,
                "lookup_component_nutrition_parallel",
                return_value=[component_food("milk", 0.3)],
            ) as retry_lookup:
                completed = call_handler("_nutrition_components", {
                    "dishName": row["nutrition"]["dishName"],
                    "primaryKey": row["nutrition"]["primaryKey"],
                    "componentInputs": row["nutrition"]["componentInputs"],
                    "componentNutritionInputs": row["nutrition"]["componentNutritionInputs"],
                })

        retried = retry_lookup.call_args.args[0]
        self.assertEqual([item["key"] for item in retried], ["milk"])
        self.assertEqual(completed["nutritionStatus"], "complete")
        row.update({
            "id": row_id,
            "name": completed["name"],
            "calories": completed["calories"],
            "points": completed["points"],
            "nutrition": {"processingStatus": "complete", "nutritionStatus": completed["nutritionStatus"]},
        })
        self.assertEqual(row["id"], "fixture-row")
        self.assertEqual(row["nutrition"]["processingStatus"], "complete")
        self.assertGreater(row["points"], 0)
        self.assertEqual(vision_call.call_count, 1)


if __name__ == "__main__":
    unittest.main()
