"""
Unit tests for broker-api-changelog-diffing-tool skill.

Fixtures are built by deep-copying a baseline and mutating exactly one thing, so a test
for "removed response field" cannot also silently drop the request body. The previous
suite hand-wrote each `new_schema`, and two of its fixtures omitted `requestBody` and
`responses` entirely — real breaking changes that passed only because the differ could
not see them.
"""
import copy
import unittest

from changelog_differ import (
    APIChangelogReport,
    BrokerAPIChangelogDiffer,
    ChangeType,
    DiffSeverity,
    SchemaDiffError,
)


BASELINE = {
    "openapi": "3.0.3",
    "paths": {
        "/v1/orders": {
            "post": {
                "parameters": [
                    {"name": "symbol", "schema": {"type": "string"}, "required": True},
                    {"name": "qty", "schema": {"type": "integer"}, "required": True},
                ],
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "order_type": {"type": "string",
                                                   "enum": ["LIMIT", "MARKET"]},
                                    "price": {"type": "number"},
                                },
                                "required": ["order_type"],
                            }
                        }
                    }
                },
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "order_id": {"type": "string"},
                                        "status": {"type": "string",
                                                   "enum": ["NEW", "FILLED"]},
                                    },
                                    "required": ["order_id", "status"],
                                }
                            }
                        }
                    }
                },
            }
        },
        "/v1/positions": {"get": {"parameters": []}},
    },
}

ORDERS_POST = ("paths", "/v1/orders", "post")


def _mutate(**_unused):
    raise NotImplementedError


class DifferTestCase(unittest.TestCase):
    def setUp(self):
        self.differ = BrokerAPIChangelogDiffer()
        self.old_schema = copy.deepcopy(BASELINE)

    @staticmethod
    def _copy():
        return copy.deepcopy(BASELINE)

    @staticmethod
    def _post(schema):
        return schema["paths"]["/v1/orders"]["post"]

    @staticmethod
    def _request_schema(schema):
        return DifferTestCase._post(schema)["requestBody"]["content"]["application/json"]["schema"]

    @staticmethod
    def _response_schema(schema):
        return DifferTestCase._post(schema)["responses"]["200"]["content"]["application/json"]["schema"]

    def _types(self, report):
        return [c.change_type for c in report.changes]


class TestEndpointDiffing(DifferTestCase):
    def test_detect_removed_endpoint(self):
        new = self._copy()
        del new["paths"]["/v1/positions"]
        report = self.differ.diff_schemas(self.old_schema, new)
        self.assertFalse(report.is_compatible)
        self.assertEqual(report.total_breaking_changes, 1)
        self.assertIn(ChangeType.REMOVED_ENDPOINT, self._types(report))

    def test_detect_removed_method(self):
        new = self._copy()
        del new["paths"]["/v1/orders"]["post"]
        report = self.differ.diff_schemas(self.old_schema, new)
        self.assertIn(ChangeType.REMOVED_ENDPOINT, self._types(report))

    def test_added_endpoint_is_non_breaking(self):
        new = self._copy()
        new["paths"]["/v1/fills"] = {"get": {"parameters": []}}
        report = self.differ.diff_schemas(self.old_schema, new)
        self.assertTrue(report.is_compatible)
        self.assertIn(ChangeType.ADDED_ENDPOINT, self._types(report))

    def test_identical_schemas_report_no_breaking_changes(self):
        report = self.differ.diff_schemas(self.old_schema, self._copy())
        self.assertTrue(report.is_compatible)
        self.assertEqual(report.total_breaking_changes, 0)

    def test_path_level_parameters_do_not_crash_and_are_diffed(self):
        """
        Regression: every key under a path was treated as an HTTP method. A Path Item
        Object legally carries `parameters`, `servers`, `summary`, `description` and
        `$ref`, and the differ raised AttributeError on a valid specification.
        """
        old = self._copy()
        old["paths"]["/v1/orders"]["parameters"] = [
            {"name": "account", "schema": {"type": "string"}, "required": True}
        ]
        old["paths"]["/v1/orders"]["summary"] = "Order operations"
        new = copy.deepcopy(old)
        new["paths"]["/v1/orders"]["parameters"] = []

        report = self.differ.diff_schemas(old, new)  # must not raise

        self.assertIn(ChangeType.REMOVED_FIELD, self._types(report))
        self.assertTrue(any("path-level" in c.path for c in report.changes))


class TestReferenceResolution(DifferTestCase):
    """
    Real broker specs describe payloads through `$ref`. A `$ref` schema carries no
    `type`, `properties` or `enum`, so a differ reading those keys directly sees an
    empty object on both sides.
    """

    @staticmethod
    def _ref_doc(order_properties, required=None):
        return {
            "openapi": "3.0.3",
            "paths": {
                "/orders": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/Order"}
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "components": {
                "schemas": {
                    "Order": {
                        "type": "object",
                        "properties": order_properties,
                        "required": required or [],
                    }
                }
            },
        }

    def test_removed_field_behind_a_ref_is_detected(self):
        """Regression: a gutted `$ref` response model reported zero changes."""
        old = self._ref_doc({
            "order_id": {"type": "string"},
            "status": {"type": "string"},
            "qty": {"type": "number"},
        })
        new = self._ref_doc({"order_id": {"type": "string"}})

        report = self.differ.diff_schemas(old, new)

        self.assertFalse(report.is_compatible)
        removed = [c for c in report.changes
                   if c.change_type is ChangeType.REMOVED_RESPONSE_FIELD]
        self.assertEqual(len(removed), 2)
        self.assertEqual(
            {c.path.rsplit(".", 1)[-1] for c in removed}, {"status", "qty"}
        )

    def test_swagger_2_definitions_refs_resolve(self):
        old = {
            "swagger": "2.0",
            "paths": {"/o": {"get": {"responses": {"200": {"content": {
                "application/json": {"schema": {"$ref": "#/definitions/Order"}}}}}}}},
            "definitions": {"Order": {"type": "object", "properties": {
                "a": {"type": "string"}, "b": {"type": "string"}}}},
        }
        new = copy.deepcopy(old)
        del new["definitions"]["Order"]["properties"]["b"]

        report = self.differ.diff_schemas(old, new)

        self.assertIn(ChangeType.REMOVED_RESPONSE_FIELD, self._types(report))

    def test_unresolvable_ref_is_reported_not_silently_skipped(self):
        old = self._ref_doc({"a": {"type": "string"}})
        old["paths"]["/orders"]["get"]["responses"]["200"]["content"][
            "application/json"]["schema"] = {"$ref": "https://example.com/Order.json"}
        new = copy.deepcopy(old)

        report = self.differ.diff_schemas(old, new)

        self.assertIn(ChangeType.UNRESOLVED_REF, self._types(report))

    def test_dangling_local_ref_is_reported(self):
        old = self._ref_doc({"a": {"type": "string"}})
        new = copy.deepcopy(old)
        del new["components"]["schemas"]["Order"]

        report = self.differ.diff_schemas(old, new)

        self.assertIn(ChangeType.UNRESOLVED_REF, self._types(report))

    def test_self_referential_model_terminates(self):
        """A recursive model must not loop forever once references resolve."""
        doc = {
            "openapi": "3.0.3",
            "paths": {"/o": {"get": {"responses": {"200": {"content": {
                "application/json": {"schema": {"$ref": "#/components/schemas/Node"}}}}}}}},
            "components": {"schemas": {"Node": {
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                    "parent": {"$ref": "#/components/schemas/Node"},
                },
            }}},
        }
        new = copy.deepcopy(doc)
        del new["components"]["schemas"]["Node"]["properties"]["value"]

        report = self.differ.diff_schemas(doc, new)  # must terminate

        self.assertIn(ChangeType.REMOVED_RESPONSE_FIELD, self._types(report))


class TestAbsenceIsAChange(DifferTestCase):
    """Removals are invisible to a differ that only walks keys present on both sides."""

    def test_removed_response_status_code_is_detected(self):
        new = self._copy()
        post = self._post(new)
        post["responses"] = {"500": post["responses"]["200"]}
        report = self.differ.diff_schemas(self.old_schema, new)
        self.assertFalse(report.is_compatible)
        self.assertIn(ChangeType.REMOVED_RESPONSE_CODE, self._types(report))

    def test_removed_response_content_type_is_detected(self):
        new = self._copy()
        resp = self._post(new)["responses"]["200"]
        resp["content"] = {"application/xml": resp["content"]["application/json"]}
        report = self.differ.diff_schemas(self.old_schema, new)
        self.assertIn(ChangeType.REMOVED_CONTENT_TYPE, self._types(report))

    def test_removed_request_content_type_is_detected(self):
        new = self._copy()
        body = self._post(new)["requestBody"]
        body["content"] = {"application/xml": body["content"]["application/json"]}
        report = self.differ.diff_schemas(self.old_schema, new)
        self.assertIn(ChangeType.REMOVED_CONTENT_TYPE, self._types(report))

    def test_removed_request_body_is_detected(self):
        new = self._copy()
        del self._post(new)["requestBody"]
        report = self.differ.diff_schemas(self.old_schema, new)
        self.assertFalse(report.is_compatible)
        self.assertIn(ChangeType.REMOVED_REQUEST_BODY, self._types(report))


class TestRequirementTransitions(DifferTestCase):
    def test_optional_parameter_becoming_required_is_detected(self):
        old = self._copy()
        old["paths"]["/v1/orders"]["post"]["parameters"][0]["required"] = False
        new = copy.deepcopy(old)
        new["paths"]["/v1/orders"]["post"]["parameters"][0]["required"] = True

        report = self.differ.diff_schemas(old, new)

        self.assertFalse(report.is_compatible)
        self.assertIn(ChangeType.NEW_REQUIRED_PARAMETER, self._types(report))

    def test_request_field_becoming_required_is_detected(self):
        new = self._copy()
        self._request_schema(new)["required"] = ["order_type", "price"]
        report = self.differ.diff_schemas(self.old_schema, new)
        self.assertIn(ChangeType.NEW_REQUIRED_PARAMETER, self._types(report))

    def test_response_field_losing_guarantee_is_detected(self):
        """A response field dropped from `required` may now be absent."""
        new = self._copy()
        self._response_schema(new)["required"] = ["order_id"]
        report = self.differ.diff_schemas(self.old_schema, new)
        self.assertFalse(report.is_compatible)
        self.assertIn(ChangeType.REQUIREMENT_MUTATION, self._types(report))

    def test_request_field_losing_requirement_is_not_breaking(self):
        new = self._copy()
        self._request_schema(new)["required"] = []
        report = self.differ.diff_schemas(self.old_schema, new)
        self.assertTrue(report.is_compatible)

    def test_new_required_request_field_is_breaking(self):
        new = self._copy()
        schema = self._request_schema(new)
        schema["properties"]["tif"] = {"type": "string"}
        schema["required"] = ["order_type", "tif"]
        report = self.differ.diff_schemas(self.old_schema, new)
        self.assertIn(ChangeType.NEW_REQUIRED_PARAMETER, self._types(report))


class TestEnumDirectionality(DifferTestCase):
    """
    A request enum constrains what the client may send; a response enum constrains what
    the client must handle. The breaking direction is opposite in each case.
    """

    def test_removed_request_enum_value_is_breaking(self):
        new = self._copy()
        self._request_schema(new)["properties"]["order_type"]["enum"] = ["MARKET"]
        report = self.differ.diff_schemas(self.old_schema, new)
        self.assertFalse(report.is_compatible)
        self.assertIn(ChangeType.ENUM_MUTATION, self._types(report))

    def test_added_request_enum_value_is_not_breaking(self):
        new = self._copy()
        self._request_schema(new)["properties"]["order_type"]["enum"] = [
            "LIMIT", "MARKET", "STOP"]
        report = self.differ.diff_schemas(self.old_schema, new)
        self.assertTrue(report.is_compatible)

    def test_added_response_enum_value_is_breaking(self):
        """Regression: a new response state broke exhaustive consumers but was not flagged."""
        new = self._copy()
        self._response_schema(new)["properties"]["status"]["enum"] = [
            "NEW", "FILLED", "EXPIRED"]
        report = self.differ.diff_schemas(self.old_schema, new)
        self.assertFalse(report.is_compatible)
        self.assertIn(ChangeType.ENUM_MUTATION, self._types(report))

    def test_removed_response_enum_value_is_informational(self):
        new = self._copy()
        self._response_schema(new)["properties"]["status"]["enum"] = ["NEW"]
        report = self.differ.diff_schemas(self.old_schema, new)
        self.assertTrue(report.is_compatible)
        self.assertIn(ChangeType.ENUM_MUTATION, self._types(report))

    def test_new_request_enum_constraint_is_breaking(self):
        new = self._copy()
        self._request_schema(new)["properties"]["price"]["enum"] = [1, 2, 3]
        report = self.differ.diff_schemas(self.old_schema, new)
        self.assertFalse(report.is_compatible)

    def test_dropped_response_enum_constraint_is_breaking(self):
        new = self._copy()
        del self._response_schema(new)["properties"]["status"]["enum"]
        report = self.differ.diff_schemas(self.old_schema, new)
        self.assertFalse(report.is_compatible)
        self.assertIn(ChangeType.ENUM_MUTATION, self._types(report))


class TestTypeMutation(DifferTestCase):
    def test_parameter_type_mutation_is_detected(self):
        new = self._copy()
        self._post(new)["parameters"][1]["schema"]["type"] = "number"
        report = self.differ.diff_schemas(self.old_schema, new)
        self.assertIn(ChangeType.TYPE_MUTATION, self._types(report))

    def test_response_type_mutation_is_detected(self):
        new = self._copy()
        self._response_schema(new)["properties"]["order_id"]["type"] = "integer"
        report = self.differ.diff_schemas(self.old_schema, new)
        self.assertIn(ChangeType.RESPONSE_TYPE_MUTATION, self._types(report))

    def test_openapi_31_type_list_equivalent_to_scalar_is_not_a_mutation(self):
        """`type: ["string"]` and `type: "string"` describe the same thing."""
        new = self._copy()
        self._response_schema(new)["properties"]["order_id"]["type"] = ["string"]
        report = self.differ.diff_schemas(self.old_schema, new)
        self.assertNotIn(ChangeType.RESPONSE_TYPE_MUTATION, self._types(report))

    def test_nullable_30_matches_type_list_31(self):
        old = self._copy()
        self._response_schema(old)["properties"]["order_id"]["nullable"] = True
        new = self._copy()
        self._response_schema(new)["properties"]["order_id"]["type"] = ["string", "null"]
        report = self.differ.diff_schemas(old, new)
        self.assertNotIn(ChangeType.RESPONSE_TYPE_MUTATION, self._types(report))

    def test_object_nested_under_type_list_is_still_diffed(self):
        """Regression risk: `type: ["object","null"]` must not skip property diffing."""
        old = self._copy()
        self._response_schema(old)["type"] = ["object", "null"]
        new = copy.deepcopy(old)
        del self._response_schema(new)["properties"]["status"]
        report = self.differ.diff_schemas(old, new)
        self.assertIn(ChangeType.REMOVED_RESPONSE_FIELD, self._types(report))

    def test_array_items_are_diffed(self):
        old = self._copy()
        self._response_schema(old)["properties"]["legs"] = {
            "type": "array",
            "items": {"type": "object", "properties": {"leg_id": {"type": "string"}}},
        }
        new = copy.deepcopy(old)
        del self._response_schema(new)["properties"]["legs"]["items"]["properties"]["leg_id"]

        report = self.differ.diff_schemas(old, new)

        self.assertIn(ChangeType.REMOVED_RESPONSE_FIELD, self._types(report))
        self.assertTrue(any("[items]" in c.path for c in report.changes))


class TestNonBreakingChanges(DifferTestCase):
    def test_added_optional_parameter_is_non_breaking(self):
        new = self._copy()
        self._post(new)["parameters"].append(
            {"name": "client_id", "schema": {"type": "string"}, "required": False}
        )
        report = self.differ.diff_schemas(self.old_schema, new)
        self.assertTrue(report.is_compatible)
        self.assertEqual(report.total_breaking_changes, 0)
        self.assertIn(ChangeType.ADDED_OPTIONAL_FIELD, self._types(report))

    def test_added_optional_request_field_is_non_breaking(self):
        new = self._copy()
        self._request_schema(new)["properties"]["tif"] = {"type": "string"}
        report = self.differ.diff_schemas(self.old_schema, new)
        self.assertTrue(report.is_compatible)

    def test_added_response_field_is_non_breaking(self):
        new = self._copy()
        self._response_schema(new)["properties"]["exchange"] = {"type": "string"}
        report = self.differ.diff_schemas(self.old_schema, new)
        self.assertTrue(report.is_compatible)


class TestInputValidation(DifferTestCase):
    def test_empty_documents_are_rejected_not_reported_compatible(self):
        """
        Regression: two empty documents produced zero changes and a green gate — exactly
        what a failed download or a wrong file path yields.
        """
        with self.assertRaises(SchemaDiffError):
            self.differ.diff_schemas({}, {})

    def test_missing_paths_rejected(self):
        with self.assertRaises(SchemaDiffError):
            self.differ.diff_schemas({"openapi": "3.0.3"}, self._copy())

    def test_non_mapping_documents_rejected(self):
        for bad in (None, [], "spec", 42):
            with self.subTest(bad=bad):
                with self.assertRaises(SchemaDiffError):
                    self.differ.diff_schemas(bad, self._copy())
                with self.assertRaises(SchemaDiffError):
                    self.differ.diff_schemas(self._copy(), bad)

    def test_paths_of_wrong_type_rejected(self):
        with self.assertRaises(SchemaDiffError):
            self.differ.diff_schemas({"paths": []}, self._copy())

    def test_path_item_of_wrong_type_rejected(self):
        with self.assertRaises(SchemaDiffError):
            self.differ.diff_schemas({"paths": {"/o": "not-an-object"}}, self._copy())


class TestReportSurface(DifferTestCase):
    def test_exit_code_reflects_compatibility(self):
        clean = self.differ.diff_schemas(self.old_schema, self._copy())
        self.assertEqual(clean.exit_code, 0)

        new = self._copy()
        del new["paths"]["/v1/positions"]
        broken = self.differ.diff_schemas(self.old_schema, new)
        self.assertEqual(broken.exit_code, 1)

    def test_breaking_changes_property_excludes_info(self):
        new = self._copy()
        del new["paths"]["/v1/positions"]
        new["paths"]["/v1/fills"] = {"get": {"parameters": []}}
        report = self.differ.diff_schemas(self.old_schema, new)

        self.assertEqual(len(report.breaking_changes), report.total_breaking_changes)
        self.assertTrue(all(c.severity is not DiffSeverity.NON_BREAKING_INFO
                            for c in report.breaking_changes))

    def test_format_report_orders_by_severity(self):
        new = self._copy()
        del new["paths"]["/v1/positions"]
        new["paths"]["/v1/fills"] = {"get": {"parameters": []}}
        text = self.differ.diff_schemas(self.old_schema, new).format_report()

        self.assertIsInstance(text, str)
        self.assertLess(text.index("CRITICAL_BREAKING"), text.index("NON_BREAKING_INFO"))

    def test_report_is_dataclass_with_expected_fields(self):
        report = self.differ.diff_schemas(self.old_schema, self._copy())
        self.assertIsInstance(report, APIChangelogReport)
        self.assertEqual(report.old_version, "v1.0")
        self.assertEqual(report.new_version, "v2.0")


if __name__ == "__main__":
    unittest.main()
