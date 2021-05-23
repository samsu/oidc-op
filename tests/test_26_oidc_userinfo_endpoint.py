import json
import os

from oidcop.configure import OPConfiguration
import pytest
from oidcmsg.oauth2 import ResponseMessage
from oidcmsg.oidc import AccessTokenRequest
from oidcmsg.oidc import AuthorizationRequest
from oidcmsg.time_util import time_sans_frac

from oidcop import user_info
from oidcop.authn_event import create_authn_event
from oidcop.cookie_handler import CookieHandler
from oidcop.oidc import userinfo
from oidcop.oidc.authorization import Authorization
from oidcop.oidc.provider_config import ProviderConfiguration
from oidcop.oidc.registration import Registration
from oidcop.oidc.token import Token
from oidcop.server import Server
from oidcop.user_authn.authn_context import INTERNETPROTOCOLPASSWORD
from oidcop.user_info import UserInfo

KEYDEFS = [
    {"type": "RSA", "key": "", "use": ["sig"]},
    {"type": "EC", "crv": "P-256", "use": ["sig"]},
]

COOKIE_KEYDEFS = [
    {"type": "oct", "kid": "sig", "use": ["sig"]},
    {"type": "oct", "kid": "enc", "use": ["enc"]},
]

RESPONSE_TYPES_SUPPORTED = [
    ["code"],
    ["token"],
    ["id_token"],
    ["code", "token"],
    ["code", "id_token"],
    ["id_token", "token"],
    ["code", "token", "id_token"],
    ["none"],
]

CAPABILITIES = {
    "subject_types_supported": ["public", "pairwise", "ephemeral"],
    "grant_types_supported": [
        "authorization_code",
        "implicit",
        "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "refresh_token",
    ],
}

AUTH_REQ = AuthorizationRequest(
    client_id="client_1",
    redirect_uri="https://example.com/cb",
    scope=["openid"],
    state="STATE",
    response_type="code",
)

TOKEN_REQ = AccessTokenRequest(
    client_id="client_1",
    redirect_uri="https://example.com/cb",
    state="STATE",
    grant_type="authorization_code",
    client_secret="hemligt",
)

TOKEN_REQ_DICT = TOKEN_REQ.to_dict()

BASEDIR = os.path.abspath(os.path.dirname(__file__))


def full_path(local_file):
    return os.path.join(BASEDIR, local_file)


USERINFO = UserInfo(json.loads(open(full_path("users.json")).read()))


class TestEndpoint(object):
    @pytest.fixture(autouse=True)
    def create_endpoint(self):
        conf = {
            "issuer": "https://example.com/",
            "password": "mycket hemligt",
            "verify_ssl": False,
            "capabilities": CAPABILITIES,
            "cookie_handler": {
                "class": CookieHandler,
                "kwargs": {
                    "keys": {"key_defs": COOKIE_KEYDEFS},
                    "name": {
                        "session": "oidc_op",
                        "register": "oidc_op_reg",
                        "session_management": "oidc_op_sman",
                    },
                },
            },
            "keys": {"uri_path": "jwks.json", "key_defs": KEYDEFS},
            "endpoint": {
                "provider_config": {
                    "path": ".well-known/openid-configuration",
                    "class": ProviderConfiguration,
                    "kwargs": {},
                },
                "registration": {"path": "registration", "class": Registration, "kwargs": {},},
                "authorization": {"path": "authorization", "class": Authorization, "kwargs": {},},
                "token": {
                    "path": "token",
                    "class": Token,
                    "kwargs": {
                        "client_authn_methods": [
                            "client_secret_post",
                            "client_secret_basic",
                            "client_secret_jwt",
                            "private_key_jwt",
                        ]
                    },
                },
                "userinfo": {
                    "path": "userinfo",
                    "class": userinfo.UserInfo,
                    "kwargs": {
                        "claim_types_supported": ["normal", "aggregated", "distributed",],
                        "client_authn_method": ["bearer_header"],
                    },
                },
            },
            "userinfo": {
                "class": user_info.UserInfo,
                "kwargs": {"db_file": full_path("users.json")},
            },
            # "client_authn": verify_client,
            "authentication": {
                "anon": {
                    "acr": INTERNETPROTOCOLPASSWORD,
                    "class": "oidcop.user_authn.user.NoAuthn",
                    "kwargs": {"user": "diana"},
                }
            },
            "template_dir": "template",
            "add_on": {
                "custom_scopes": {
                    "function": "oidcop.oidc.add_on.custom_scopes.add_custom_scopes",
                    "kwargs": {
                        "research_and_scholarship": [
                            "name",
                            "given_name",
                            "family_name",
                            "email",
                            "email_verified",
                            "sub",
                            "eduperson_scoped_affiliation",
                        ]
                    },
                }
            },
        }
        server = Server(OPConfiguration(conf=conf, base_path=BASEDIR), cwd=BASEDIR)

        endpoint_context = server.endpoint_context
        endpoint_context.cdb["client_1"] = {
            "client_secret": "hemligt",
            "redirect_uris": [("https://example.com/cb", None)],
            "client_salt": "salted",
            "token_endpoint_auth_method": "client_secret_post",
            "response_types": ["code", "token", "code id_token", "id_token"],
        }
        self.endpoint = server.server_get("endpoint", "userinfo")
        self.session_manager = endpoint_context.session_manager
        self.user_id = "diana"

    def _create_session(self, auth_req, sub_type="public", sector_identifier=""):
        if sector_identifier:
            authz_req = auth_req.copy()
            authz_req["sector_identifier_uri"] = sector_identifier
        else:
            authz_req = auth_req
        client_id = authz_req["client_id"]
        ae = create_authn_event(self.user_id)
        return self.session_manager.create_session(
            ae, authz_req, self.user_id, client_id=client_id, sub_type=sub_type
        )

    def _mint_code(self, grant, session_id):
        # Constructing an authorization code is now done
        return grant.mint_token(
            session_id=session_id,
            endpoint_context=self.endpoint.server_get("endpoint_context"),
            token_type="authorization_code",
            token_handler=self.session_manager.token_handler["code"],
            expires_at=time_sans_frac() + 300,  # 5 minutes from now
        )

    def _mint_token(self, token_type, grant, session_id, token_ref=None):
        _session_info = self.session_manager.get_session_info(session_id, grant=True)
        return grant.mint_token(
            session_id=session_id,
            endpoint_context=self.endpoint.server_get("endpoint_context"),
            token_type=token_type,
            token_handler=self.session_manager.token_handler[token_type],
            expires_at=time_sans_frac() + 900,  # 15 minutes from now
            based_on=token_ref,  # Means the token (tok) was used to mint this token
        )

    def test_init(self):
        assert self.endpoint
        assert set(
            self.endpoint.server_get("endpoint_context").provider_info["claims_supported"]
        ) == {
            "address",
            "birthdate",
            "email",
            "email_verified",
            "eduperson_scoped_affiliation",
            "family_name",
            "gender",
            "given_name",
            "locale",
            "middle_name",
            "name",
            "nickname",
            "phone_number",
            "phone_number_verified",
            "picture",
            "preferred_username",
            "profile",
            "sub",
            "updated_at",
            "website",
            "zoneinfo",
        }

    def test_parse(self):
        session_id = self._create_session(AUTH_REQ)
        grant = self.session_manager[session_id]

        # Free standing access token, not based on an authorization code
        access_token = self._mint_token("access_token", grant, session_id)
        http_info = {"headers": {"authorization": "Bearer {}".format(access_token.value)}}
        _req = self.endpoint.parse_request({}, http_info=http_info)
        assert set(_req.keys()) == {"client_id", "access_token"}
        assert _req["client_id"] == AUTH_REQ["client_id"]
        assert _req["access_token"] == access_token.value

    def test_parse_invalid_token(self):
        http_info = {"headers": {"authorization": "Bearer invalid"}}
        _req = self.endpoint.parse_request({}, http_info=http_info)
        assert _req["error"] == "invalid_token"

    def test_process_request(self):
        session_id = self._create_session(AUTH_REQ)
        grant = self.session_manager[session_id]
        code = self._mint_code(grant, session_id)
        access_token = self._mint_token("access_token", grant, session_id, code)

        http_info = {"headers": {"authorization": "Bearer {}".format(access_token.value)}}

        _req = self.endpoint.parse_request({}, http_info=http_info)
        args = self.endpoint.process_request(_req, http_info=http_info)
        assert args

    def test_process_request_not_allowed(self):
        session_id = self._create_session(AUTH_REQ)
        grant = self.session_manager[session_id]
        code = self._mint_code(grant, session_id)
        access_token = self._mint_token("access_token", grant, session_id, code)

        # 2 things can make the request invalid.
        # 1) The token is not valid anymore or 2) The event is not valid.
        _event = grant.authentication_event
        _event["authn_time"] -= 9000
        _event["valid_until"] -= 9000

        http_info = {"headers": {"authorization": "Bearer {}".format(access_token.value)}}
        _req = self.endpoint.parse_request({}, http_info=http_info)

        args = self.endpoint.process_request(_req, http_info=http_info)
        assert set(args["response_args"].keys()) == {"error", "error_description"}

    def test_do_response(self):
        session_id = self._create_session(AUTH_REQ)
        grant = self.session_manager[session_id]
        code = self._mint_code(grant, session_id)
        access_token = self._mint_token("access_token", grant, session_id, code)

        http_info = {"headers": {"authorization": "Bearer {}".format(access_token.value)}}
        _req = self.endpoint.parse_request({}, http_info=http_info)

        args = self.endpoint.process_request(_req)
        assert args
        res = self.endpoint.do_response(request=_req, **args)
        assert res

    def test_do_signed_response(self):
        self.endpoint.server_get("endpoint_context").cdb["client_1"][
            "userinfo_signed_response_alg"
        ] = "ES256"

        session_id = self._create_session(AUTH_REQ)
        grant = self.session_manager[session_id]
        code = self._mint_code(grant, session_id)
        access_token = self._mint_token("access_token", grant, session_id, code)

        http_info = {"headers": {"authorization": "Bearer {}".format(access_token.value)}}
        _req = self.endpoint.parse_request({}, http_info=http_info)

        args = self.endpoint.process_request(_req)
        assert args
        res = self.endpoint.do_response(request=_req, **args)
        assert res

    def test_custom_scope(self):
        _auth_req = AUTH_REQ.copy()
        _auth_req["scope"] = ["openid", "research_and_scholarship"]

        session_id = self._create_session(_auth_req)
        grant = self.session_manager[session_id]
        access_token = self._mint_token("access_token", grant, session_id)

        self.endpoint.kwargs["add_claims_by_scope"] = True
        self.endpoint.server_get("endpoint_context").claims_interface.add_claims_by_scope = True
        grant.claims = {
            "userinfo": self.endpoint.server_get("endpoint_context").claims_interface.get_claims(
                session_id=session_id, scopes=_auth_req["scope"], usage="userinfo"
            )
        }

        http_info = {"headers": {"authorization": "Bearer {}".format(access_token.value)}}
        _req = self.endpoint.parse_request({}, http_info=http_info)
        args = self.endpoint.process_request(_req, http_info=http_info)

        assert set(args["response_args"].keys()) == {
            "eduperson_scoped_affiliation",
            "given_name",
            "email_verified",
            "email",
            "family_name",
            "name",
            "sub",
        }

    def test_wrong_type_of_token(self):
        _auth_req = AUTH_REQ.copy()
        _auth_req["scope"] = ["openid", "research_and_scholarship"]

        session_id = self._create_session(_auth_req)
        grant = self.session_manager[session_id]
        refresh_token = self._mint_token("refresh_token", grant, session_id)

        http_info = {"headers": {"authorization": "Bearer {}".format(refresh_token.value)}}
        _req = self.endpoint.parse_request({}, http_info=http_info)
        args = self.endpoint.process_request(_req, http_info=http_info)

        assert isinstance(args, ResponseMessage)
        assert args["error_description"] == "Wrong type of token"

    def test_invalid_token(self):
        _auth_req = AUTH_REQ.copy()
        _auth_req["scope"] = ["openid", "research_and_scholarship"]

        session_id = self._create_session(_auth_req)
        grant = self.session_manager[session_id]
        access_token = self._mint_token("access_token", grant, session_id)

        http_info = {"headers": {"authorization": "Bearer {}".format(access_token.value)}}
        _req = self.endpoint.parse_request({}, http_info=http_info)

        access_token.expires_at = time_sans_frac() - 10
        args = self.endpoint.process_request(_req)

        assert isinstance(args, ResponseMessage)
        assert args["error_description"] == "Invalid Token"
