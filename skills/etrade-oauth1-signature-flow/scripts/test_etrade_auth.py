"""
Unit tests for etrade-oauth1-signature-flow skill.

The signature-correctness tests use externally published vectors rather than
values produced by this implementation:

  - RFC 5849 Section 3.4.1.1 publishes a complete worked signature base string.
    It exercises query-component parameters, a repeated parameter name, a
    ``realm`` that must be excluded, and encode-then-sort ordering.
  - The X (formerly Twitter) OAuth 1.0a documentation publishes a base string,
    signing key, and the resulting ``oauth_signature`` value, which pins the
    HMAC-SHA1 and base64 steps end to end.
"""
import unittest
from etrade_auth import ETradeAuthError, ETradeOAuth1Client, OAuth1Credentials

# RFC 5849 Section 3.4.1.1 worked example (line breaks in the RFC are for
# display only and are removed here).
RFC5849_BASE_STRING = (
    "POST&http%3A%2F%2Fexample.com%2Frequest&a2%3Dr%2520b%26a3%3D2%2520q"
    "%26a3%3Da%26b5%3D%253D%25253D%26c%2540%3D%26c2%3D%26oauth_consumer_"
    "key%3D9djdj82h48djs9d2%26oauth_nonce%3D7d8f3e4a%26oauth_signature_m"
    "ethod%3DHMAC-SHA1%26oauth_timestamp%3D137131201%26oauth_token%3Dkkk"
    "9d7dh3k39sjv7"
)
RFC5849_URL = "http://example.com/request?b5=%3D%253D&a3=a&c%40=&a2=r%20b"

# X OAuth 1.0a documented example.
X_BASE_STRING = (
    "POST&https%3A%2F%2Fapi.x.com%2F1.1%2Fstatuses%2Fupdate.json&"
    "include_entities%3Dtrue%26oauth_consumer_key%3Dxvz1evFS4wEEPTGEFPHBog"
    "%26oauth_nonce%3DkYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg"
    "%26oauth_signature_method%3DHMAC-SHA1%26oauth_timestamp%3D1318622958"
    "%26oauth_token%3D370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb"
    "%26oauth_version%3D1.0%26status%3DHello%2520Ladies%2520%252B%2520"
    "Gentlemen%252C%2520a%2520signed%2520OAuth%2520request%2521"
)
X_CONSUMER_SECRET = "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw"
X_TOKEN_SECRET = "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE"
X_EXPECTED_SIGNATURE = "Ls93hJiZbQ3akF3HF3x1Bz8/zU4="


class TestETradeOAuth1Client(unittest.TestCase):

    def setUp(self):
        self.client = ETradeOAuth1Client(
            consumer_key="test_consumer_key",
            consumer_secret="test_consumer_secret",
            use_sandbox=True,
        )


class TestPercentEncoding(TestETradeOAuth1Client):

    def test_unreserved_characters_are_never_encoded(self):
        """RFC 5849 Section 3.6: ALPHA/DIGIT/-/./_/~ must pass through."""
        unreserved = "abcXYZ019-._~"
        self.assertEqual(self.client.percent_encode(unreserved), unreserved)

    def test_reserved_characters_are_encoded_uppercase_hex(self):
        self.assertEqual(self.client.percent_encode(" "), "%20")
        self.assertEqual(self.client.percent_encode("+"), "%2B")
        self.assertEqual(self.client.percent_encode("/"), "%2F")
        self.assertEqual(self.client.percent_encode("="), "%3D")
        self.assertEqual(self.client.percent_encode("&"), "%26")

    def test_non_ascii_is_utf8_encoded(self):
        """Non-ASCII must be UTF-8 encoded before percent-encoding."""
        self.assertEqual(self.client.percent_encode("é"), "%C3%A9")


class TestBaseStringUriNormalization(TestETradeOAuth1Client):

    def test_scheme_and_host_are_lowercased(self):
        """RFC 5849 Section 3.4.1.2: scheme and host MUST be lowercase."""
        self.assertEqual(
            self.client.normalize_base_string_uri("HTTPS://API.ETRADE.COM/v1/Accounts"),
            "https://api.etrade.com/v1/Accounts",
        )

    def test_default_port_is_stripped_and_custom_port_kept(self):
        self.assertEqual(
            self.client.normalize_base_string_uri("https://api.etrade.com:443/v1/accounts"),
            "https://api.etrade.com/v1/accounts",
        )
        self.assertEqual(
            self.client.normalize_base_string_uri("https://api.etrade.com:8443/v1/accounts"),
            "https://api.etrade.com:8443/v1/accounts",
        )

    def test_query_and_fragment_are_excluded_from_the_uri(self):
        self.assertEqual(
            self.client.normalize_base_string_uri(
                "https://api.etrade.com/v1/market/quote/AAPL?detailFlag=ALL#frag"
            ),
            "https://api.etrade.com/v1/market/quote/AAPL",
        )

    def test_non_http_scheme_rejected(self):
        with self.assertRaises(ETradeAuthError):
            self.client.normalize_base_string_uri("ftp://api.etrade.com/v1/accounts")

    def test_missing_host_rejected(self):
        with self.assertRaises(ETradeAuthError):
            self.client.normalize_base_string_uri("/v1/accounts")

    def test_empty_url_rejected(self):
        with self.assertRaises(ETradeAuthError):
            self.client.normalize_base_string_uri("   ")


class TestBaseStringConstruction(TestETradeOAuth1Client):

    def test_matches_rfc5849_published_example(self):
        """Reproduce the base string published in RFC 5849 Section 3.4.1.1.

        Regression guard: an implementation that percent-encodes the whole URL
        (query included) into the base string URI, or that omits the query
        parameters from the parameter string, cannot produce this value.
        """
        params = [
            ("realm", "Example"),  # MUST be excluded
            ("oauth_consumer_key", "9djdj82h48djs9d2"),
            ("oauth_token", "kkk9d7dh3k39sjv7"),
            ("oauth_signature_method", "HMAC-SHA1"),
            ("oauth_timestamp", "137131201"),
            ("oauth_nonce", "7d8f3e4a"),
            ("c2", ""),        # form-encoded body
            ("a3", "2 q"),     # form-encoded body; repeats a query-string name
        ]
        self.assertEqual(
            self.client.build_base_string("POST", RFC5849_URL, params),
            RFC5849_BASE_STRING,
        )

    def test_query_parameters_participate_in_the_signature(self):
        """A query parameter must appear in the parameter string, and the base
        string URI must not carry the query."""
        base = self.client.build_base_string(
            "GET",
            "https://api.etrade.com/v1/market/quote/AAPL?detailFlag=ALL",
            {"oauth_nonce": "n"},
        )
        self.assertIn("detailFlag%3DALL", base)
        self.assertNotIn("%3FdetailFlag", base)

    def test_signature_differs_when_query_parameters_differ(self):
        """Two URLs differing only in the query must sign differently."""
        params = {"oauth_nonce": "n", "oauth_timestamp": "1"}
        a = self.client.build_base_string(
            "GET", "https://api.etrade.com/v1/accounts/X/orders?status=OPEN", params
        )
        b = self.client.build_base_string(
            "GET", "https://api.etrade.com/v1/accounts/X/orders?status=EXECUTED", params
        )
        self.assertNotEqual(a, b)

    def test_oauth_signature_is_excluded(self):
        base = self.client.build_base_string(
            "GET",
            "https://api.etrade.com/v1/accounts",
            {"oauth_nonce": "n", "oauth_signature": "should-not-appear"},
        )
        self.assertNotIn("oauth_signature", base)

    def test_parameters_are_sorted_after_encoding_not_before(self):
        """Encode-then-sort ordering (RFC 5849 Section 3.4.1.3.2).

        Raw ``"a b" < "a+"`` because 0x20 < 0x2B, but encoded
        ``"a%2B" < "a%20b"`` is false — encoded, ``a%20b`` sorts first. Sorting
        before encoding would swap these two.
        """
        base = self.client.build_base_string(
            "GET", "https://api.etrade.com/v1/x", [("p", "a b"), ("p", "a+")]
        )
        # Encoded values are "a%20b" and "a%2B"; byte-wise "a%20b" sorts first.
        self.assertEqual(
            self.client.normalize_parameters([("p", "a b"), ("p", "a+")]),
            "p=a%20b&p=a%2B",
        )
        # Reversing the input order must not change the normalized output.
        self.assertEqual(
            self.client.normalize_parameters([("p", "a+"), ("p", "a b")]),
            "p=a%20b&p=a%2B",
        )
        self.assertTrue(base.startswith("GET&"))

    def test_blank_query_value_is_retained(self):
        self.assertEqual(
            self.client.normalize_parameters(
                self.client.collect_query_parameters(
                    "https://api.etrade.com/v1/x?flag=&other=1"
                )
            ),
            "flag=&other=1",
        )

    def test_valueless_query_parameter_is_a_parameter_with_an_empty_value(self):
        """RFC 5849's own example carries a valueless parameter; ``?flag`` and
        ``?flag=`` must both normalize to ``flag=``, and a stray empty field
        must not become one."""
        self.assertEqual(
            self.client.collect_query_parameters("https://api.etrade.com/v1/x?flag"),
            [("flag", "")],
        )
        self.assertEqual(
            self.client.collect_query_parameters("https://api.etrade.com/v1/x?a=1&&b=2"),
            [("a", "1"), ("b", "2")],
        )

    def test_malformed_params_raise_etrade_auth_error(self):
        """Bad caller input must surface as this module's error type, not as a
        bare unpacking ValueError/TypeError."""
        for bad in ([("a",)], [("a", "b", "c")], "notpairs", 5):
            with self.assertRaises(ETradeAuthError, msg=repr(bad)):
                self.client.build_base_string("GET", "https://api.etrade.com/v1/x", bad)

    def test_method_is_uppercased(self):
        base = self.client.build_base_string("get", "https://api.etrade.com/v1/x", {})
        self.assertTrue(base.startswith("GET&"))

    def test_empty_method_rejected(self):
        with self.assertRaises(ETradeAuthError):
            self.client.build_base_string("", "https://api.etrade.com/v1/x", {})


class TestHmacSha1Signing(TestETradeOAuth1Client):

    def test_matches_published_x_oauth_signature(self):
        """Reproduce the signature published in X's OAuth 1.0a documentation."""
        sig = self.client.sign_hmac_sha1(
            X_BASE_STRING, X_CONSUMER_SECRET, X_TOKEN_SECRET
        )
        self.assertEqual(sig, X_EXPECTED_SIGNATURE)

    def test_end_to_end_base_string_then_signature(self):
        """Build X's base string from raw inputs, then sign it."""
        base = self.client.build_base_string(
            "POST",
            "https://api.x.com/1.1/statuses/update.json?include_entities=true",
            [
                ("status", "Hello Ladies + Gentlemen, a signed OAuth request!"),
                ("oauth_consumer_key", "xvz1evFS4wEEPTGEFPHBog"),
                ("oauth_nonce", "kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg"),
                ("oauth_signature_method", "HMAC-SHA1"),
                ("oauth_timestamp", "1318622958"),
                ("oauth_token",
                 "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb"),
                ("oauth_version", "1.0"),
            ],
        )
        self.assertEqual(base, X_BASE_STRING)
        self.assertEqual(
            self.client.sign_hmac_sha1(base, X_CONSUMER_SECRET, X_TOKEN_SECRET),
            X_EXPECTED_SIGNATURE,
        )

    def test_signing_key_keeps_trailing_ampersand_without_token_secret(self):
        """Leg one has no token secret; the key must still end in '&'.

        Verified by signing with an explicit empty token secret and with the
        default, which must agree, and by confirming a non-empty token secret
        changes the result.
        """
        explicit = self.client.sign_hmac_sha1("base", "cs", "")
        default = self.client.sign_hmac_sha1("base", "cs")
        self.assertEqual(explicit, default)
        self.assertNotEqual(explicit, self.client.sign_hmac_sha1("base", "cs", "ts"))

    def test_secrets_are_percent_encoded_into_the_signing_key(self):
        """A secret containing a reserved character must be encoded, so the
        encoded and raw forms cannot produce the same signature."""
        self.assertNotEqual(
            self.client.sign_hmac_sha1("base", "a b", ""),
            self.client.sign_hmac_sha1("base", "a%20b", ""),
        )


class TestAuthHeader(TestETradeOAuth1Client):

    def test_auth_header_format(self):
        """Authorization header should start with 'OAuth ' and contain required params."""
        header = self.client.build_auth_header(
            "GET", "https://api.etrade.com/v1/accounts",
            token="access_token_123",
            token_secret="access_secret_456",
        )
        self.assertTrue(header.startswith("OAuth "))
        for expected in (
            "oauth_consumer_key", "oauth_signature", "oauth_nonce",
            "oauth_timestamp", "oauth_token", "oauth_signature_method",
            "oauth_version",
        ):
            self.assertIn(expected, header)

    def test_signature_value_is_percent_encoded_in_the_header(self):
        """Base64 signatures contain '+', '/' and '=', all of which must be
        escaped inside the header's quoted value."""
        for _ in range(40):
            header = self.client.build_auth_header(
                "GET", "https://api.etrade.com/v1/accounts",
                token="t", token_secret="s",
            )
            sig = header.split('oauth_signature="')[1].split('"')[0]
            self.assertNotIn("+", sig)
            self.assertNotIn("/", sig)
            self.assertNotIn("=", sig)

    def test_request_token_header_carries_oob_callback(self):
        """E*TRADE requires oauth_callback on the request-token call, always 'oob'."""
        header = self.client.build_request_token_header()
        self.assertIn('oauth_callback="oob"', header)
        self.assertNotIn("oauth_token=", header)

    def test_callback_absent_by_default(self):
        header = self.client.build_auth_header("GET", "https://api.etrade.com/v1/x")
        self.assertNotIn("oauth_callback", header)

    def test_extra_params_are_signed_but_not_emitted_in_the_header(self):
        """RFC 5849 Section 3.5.1: only oauth_* parameters belong in the header."""
        header = self.client.build_auth_header(
            "POST", "https://api.etrade.com/v1/x",
            token="t", token_secret="s",
            extra_params={"symbol": "AAPL"},
        )
        self.assertNotIn("symbol", header)

    def test_nonce_is_unique_across_calls(self):
        nonces = {self.client.generate_nonce() for _ in range(1000)}
        self.assertEqual(len(nonces), 1000)


class TestEndpointsAndFlow(TestETradeOAuth1Client):

    def test_sandbox_and_production_hosts(self):
        self.assertEqual(
            self.client.get_request_token_url(),
            "https://apisb.etrade.com/oauth/request_token",
        )
        live = ETradeOAuth1Client("k", "s", use_sandbox=False)
        self.assertEqual(
            live.get_access_token_url(), "https://api.etrade.com/oauth/access_token"
        )
        self.assertEqual(
            live.get_renew_access_token_url(),
            "https://api.etrade.com/oauth/renew_access_token",
        )
        self.assertEqual(
            live.get_revoke_access_token_url(),
            "https://api.etrade.com/oauth/revoke_access_token",
        )

    def test_authorize_url_uses_us_etrade_host_in_both_environments(self):
        """The authorize page is on us.etrade.com for sandbox AND production."""
        for sandbox in (True, False):
            client = ETradeOAuth1Client("ck", "cs", use_sandbox=sandbox)
            client.set_request_token("tok", "sec")
            url = client.get_authorize_url()
            self.assertTrue(
                url.startswith("https://us.etrade.com/e/t/etws/authorize?"), url
            )
            self.assertNotIn("apisb.etrade.com", url)

    def test_authorize_url_percent_encodes_the_request_token(self):
        """Request tokens contain '+', '/' and '=' and must be escaped."""
        self.client.set_request_token("a+b/c=", "sec")
        url = self.client.get_authorize_url()
        self.assertIn("token=a%2Bb%2Fc%3D", url)
        self.assertIn("key=test_consumer_key", url)

    def test_authorize_url_requires_a_request_token(self):
        with self.assertRaises(ETradeAuthError):
            self.client.get_authorize_url()

    def test_access_token_header_requires_request_token_and_verifier(self):
        with self.assertRaises(ETradeAuthError):
            self.client.build_access_token_header("VERIF1")
        self.client.set_request_token("rtok", "rsec")
        with self.assertRaises(ETradeAuthError):
            self.client.build_access_token_header("   ")
        header = self.client.build_access_token_header("VERIF1")
        self.assertIn('oauth_verifier="VERIF1"', header)
        self.assertIn('oauth_token="rtok"', header)

    def test_parse_token_response_extracts_token_pair(self):
        token = ETradeOAuth1Client.parse_token_response(
            "oauth_token=abc%2B1&oauth_token_secret=xyz%3D&oauth_callback_confirmed=true"
        )
        self.assertEqual(token.token, "abc+1")
        self.assertEqual(token.token_secret, "xyz=")

    def test_parse_token_response_rejects_error_bodies(self):
        """An error body decodes cleanly but has no token; accepting it would
        leave the client signing with empty credentials."""
        for body in (
            "",
            "   ",
            "oauth_problem=signature_invalid",
            "oauth_token=abc",
            "oauth_token=&oauth_token_secret=xyz",
        ):
            with self.assertRaises(ETradeAuthError, msg=body):
                ETradeOAuth1Client.parse_token_response(body)


class TestSignRequest(TestETradeOAuth1Client):

    def test_sign_request_requires_access_token(self):
        """sign_request should raise if access token is not set."""
        with self.assertRaises(ValueError):
            self.client.sign_request("GET", "https://api.etrade.com/v1/accounts")

    def test_sign_request_with_access_token(self):
        """sign_request should return valid Authorization header after setting token."""
        self.client.set_access_token("token_abc", "secret_xyz")
        headers = self.client.sign_request("GET", "https://api.etrade.com/v1/accounts")
        self.assertIn("Authorization", headers)
        self.assertTrue(headers["Authorization"].startswith("OAuth "))

    def test_sign_request_signs_query_parameters(self):
        self.client.set_access_token("token_abc", "secret_xyz")
        headers = self.client.sign_request(
            "GET", "https://api.etrade.com/v1/market/quote/AAPL?detailFlag=ALL"
        )
        self.assertIn("oauth_signature", headers["Authorization"])

    def test_renew_and_revoke_target_the_right_endpoints(self):
        self.client.set_access_token("token_abc", "secret_xyz")
        self.assertIn("Authorization", self.client.sign_renew_access_token())
        self.assertIn("Authorization", self.client.sign_revoke_access_token())

    def test_renew_requires_an_access_token(self):
        with self.assertRaises(ETradeAuthError):
            self.client.sign_renew_access_token()


class TestCredentialValidationAndSecrecy(TestETradeOAuth1Client):

    def test_blank_consumer_credentials_rejected(self):
        for key, secret in (("", "s"), ("   ", "s"), ("k", ""), ("k", "   ")):
            with self.assertRaises(ETradeAuthError):
                ETradeOAuth1Client(key, secret)

    def test_blank_tokens_rejected(self):
        with self.assertRaises(ETradeAuthError):
            self.client.set_access_token("", "secret")
        with self.assertRaises(ETradeAuthError):
            self.client.set_request_token("tok", "")

    def test_secrets_are_not_exposed_in_repr(self):
        """repr() must not leak secrets into logs or tracebacks."""
        creds = OAuth1Credentials(
            consumer_key="ck",
            consumer_secret="SUPER_SECRET",
            access_token="at",
            access_token_secret="ACCESS_SECRET",
        )
        text = repr(creds)
        self.assertNotIn("SUPER_SECRET", text)
        self.assertNotIn("ACCESS_SECRET", text)
        self.assertIn("ck", text)

    def test_documented_etrade_lifetimes(self):
        """Values quoted in SKILL.md and references/standards.md."""
        self.assertEqual(ETradeOAuth1Client.REQUEST_TOKEN_TTL_SECONDS, 300)
        self.assertEqual(ETradeOAuth1Client.ACCESS_TOKEN_IDLE_INACTIVATION_SECONDS, 7200)
        self.assertEqual(ETradeOAuth1Client.TIMESTAMP_TOLERANCE_SECONDS, 300)


if __name__ == "__main__":
    unittest.main()
