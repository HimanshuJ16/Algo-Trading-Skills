"""
Unit tests for broker-api-changelog-diffing-tool skill.
"""
import unittest
from changelog_differ import BrokerAPIChangelogDiffer, ChangeType, DiffSeverity

class TestBrokerAPIChangelogDiffer(unittest.TestCase):
    def setUp(self):
        self.differ = BrokerAPIChangelogDiffer()
        self.old_schema = {
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
                                            "order_type": {"type": "string", "enum": ["LIMIT", "MARKET"]},
                                            "price": {"type": "number"}
                                        },
                                        "required": ["order_type"]
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
                                                "status": {"type": "string"}
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                },
                "/v1/positions": {
                    "get": {"parameters": []}
                }
            }
        }

    def test_detect_removed_endpoint(self):
        new_schema = {
            "paths": {
                "/v1/orders": self.old_schema["paths"]["/v1/orders"]
            }
        }
        report = self.differ.diff_schemas(self.old_schema, new_schema)
        self.assertFalse(report.is_compatible)
        self.assertEqual(report.total_breaking_changes, 1)
        self.assertEqual(report.changes[0].change_type, ChangeType.REMOVED_ENDPOINT)

    def test_type_mutation_and_enum_removal(self):
        new_schema = {
            "paths": {
                "/v1/orders": {
                    "post": {
                        "parameters": [
                            {"name": "symbol", "schema": {"type": "string"}, "required": True},
                            {"name": "qty", "schema": {"type": "number"}, "required": True}, # type mutation
                        ],
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "order_type": {"type": "string", "enum": ["MARKET"]}, # LIMIT removed
                                            "price": {"type": "number"},
                                            "time_in_force": {"type": "string"} # Added optional
                                        },
                                        "required": ["order_type", "time_in_force"] # Made time_in_force required
                                    }
                                }
                            }
                        }
                    }
                },
                "/v1/positions": {"get": {"parameters": []}}
            }
        }
        report = self.differ.diff_schemas(self.old_schema, new_schema)
        self.assertFalse(report.is_compatible)
        types = [c.change_type for c in report.changes]
        self.assertIn(ChangeType.TYPE_MUTATION, types)
        self.assertIn(ChangeType.ENUM_MUTATION, types)
        self.assertIn(ChangeType.NEW_REQUIRED_PARAMETER, types)

    def test_removed_response_field(self):
        new_schema = {
            "paths": {
                "/v1/orders": {
                    "post": {
                        "parameters": self.old_schema["paths"]["/v1/orders"]["post"]["parameters"],
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "order_id": {"type": "string"} # status removed
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                },
                "/v1/positions": {"get": {"parameters": []}}
            }
        }
        report = self.differ.diff_schemas(self.old_schema, new_schema)
        self.assertFalse(report.is_compatible)
        self.assertEqual(report.changes[0].change_type, ChangeType.REMOVED_RESPONSE_FIELD)

    def test_non_breaking_optional_field(self):
        new_schema = {
            "paths": {
                "/v1/orders": {
                    "post": {
                        "parameters": [
                            {"name": "symbol", "schema": {"type": "string"}, "required": True},
                            {"name": "qty", "schema": {"type": "integer"}, "required": True},
                            {"name": "client_id", "schema": {"type": "string"}, "required": False}, # Added optional
                        ]
                    }
                },
                "/v1/positions": {"get": {"parameters": []}}
            }
        }
        report = self.differ.diff_schemas(self.old_schema, new_schema)
        self.assertTrue(report.is_compatible)
        self.assertEqual(report.total_breaking_changes, 0)
        self.assertEqual(report.changes[0].change_type, ChangeType.ADDED_OPTIONAL_FIELD)

if __name__ == "__main__":
    unittest.main()
