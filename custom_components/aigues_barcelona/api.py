import base64
import datetime
import json
import logging

import requests

from .const import API_COOKIE_TOKEN
from .const import API_HOSTS
from .version import VERSION

TIMEOUT = 60

_LOGGER: logging.Logger = logging.getLogger(__name__)


class AiguesApiClient:
    def __init__(
        self, username, password, contract=None, provider="agbar", session: requests.Session = None, cookie=None
    ):
        from .const import API_HOSTS
        if session is None:
            session = requests.Session()
        self.cli = session
        self.api_host = f"https://{API_HOSTS.get(provider, API_HOSTS['agbar'])}"
        self.provider = provider
        self.headers = {
            "Ocp-Apim-Subscription-Key": "3cca6060fee14bffa3450b19941bd954",
            "Ocp-Apim-Trace": "false",
            "Content-Type": "application/json; charset=UTF-8",
            "User-Agent": f"hass-aigues-barcelona/{VERSION} (Home Assistant)",
        }
        self._username = username
        self._password = password
        self._contract = contract
        self.last_response = None
        self._cookie = cookie

    def _generate_url(self, path, query) -> str:
        query_proc = ""
        if query:
            query_proc = "?" + "&".join([f"{k}={v}" for k, v in query.items()])
        return f"{self.api_host}/{path.lstrip('/')}{query_proc}"

    def _return_token_field(self, key):
        token = self.cli.cookies.get_dict().get(API_COOKIE_TOKEN)
        if not token:
            _LOGGER.warning("Token login missing")
            return False

        data = token.split(".")[1]
        _LOGGER.debug(data)
        # add padding to avoid failures
        data = base64.urlsafe_b64decode(data + "==")

        return json.loads(data).get(key)

    def _query(self, path, query=None, json=None, headers=None, method="GET"):
        if headers is None:
            headers = dict()
        headers = {**self.headers, **headers}

        resp = self.cli.request(
            method=method,
            url=self._generate_url(path, query),
            json=json,
            headers=headers,
            timeout=TIMEOUT,
        )
        _LOGGER.debug(f"Query done with code {resp.status_code}")
        msg = resp.text
        self.last_response = resp.text
        if len(msg) > 5 and (msg.startswith("{") or msg.startswith("[")):
            msg = resp.json()
            if isinstance(msg, list) and len(msg) == 1:
                msg = msg[0]
            self.last_response = msg.copy()
            msg = msg.get("message", resp.text)

        if resp.status_code == 500:
            raise Exception(f"Server error: {msg}")
        if resp.status_code == 404:
            raise Exception(f"Not found: {msg}")
        if resp.status_code == 401:
            raise Exception(f"Denied: {msg}")
        if resp.status_code == 400:
            raise Exception(f"Bad response: {msg}")
        if resp.status_code == 429:
            raise Exception(f"Rate-Limited: {msg}")

        return resp

    def login(self, user=None, password=None, recaptcha=None):
        if user is None:
            user = self._username
        if password is None:
            password = self._password
        # recaptcha seems to not be validated?
        if recaptcha is None:
            recaptcha = ""

        path = "/ofex-login-api/auth/getToken"
        query = {"lang": "ca", "recaptchaClientResponse": recaptcha}
        body = {
            "scope": "ofex",
            "companyIdentification": "",
            "userIdentification": user,
            "password": password,
        }
        headers = {
            "Content-Type": "application/json",
            "Ocp-Apim-Subscription-Key": "6a98b8b8c7b243cda682a43f09e6588b;product=portlet-login-ofex",
        }

        r = self._query(path, query, body, headers, method="POST")

        _LOGGER.debug(r)
        error = r.json().get("errorMessage", None)
        if error:
            _LOGGER.warning(error)
            return False

        access_token = r.json().get("access_token", None)
        if not access_token:
            _LOGGER.warning("Access token missing")
            return False

        return True

        # set as cookie: ofexTokenJwt
        # https://www.aiguesdebarcelona.cat/ca/area-clientes

    def set_token(self, token: str):
        host = ".".join(self.api_host.split(".")[1:])
        cookie_data = {
            "name": API_COOKIE_TOKEN,
            "value": token,
            "domain": f".{host}",
            "path": "/",
            "secure": True,
            "rest": {"HttpOnly": True, "SameSite": "None"},
        }
        cookie = requests.cookies.create_cookie(**cookie_data)
        return self.cli.cookies.set_cookie(cookie)

    def is_token_expired(self) -> bool:
        """Check if Token in cookie has expired or not."""
        expires = self._return_token_field("exp")
        if not expires:
            return True

        expires = datetime.datetime.fromtimestamp(expires)
        NOW = datetime.datetime.now()

        return NOW >= expires

    def profile(self, user=None):
        if user is None:
            user = self._return_token_field("name")

        path = "/ofex-login-api/auth/getProfile"
        query = {"lang": "ca", "userId": user, "clientId": user}
        headers = {
            "Ocp-Apim-Subscription-Key": "6a98b8b8c7b243cda682a43f09e6588b;product=portlet-login-ofex"
        }

        r = self._query(path, query, headers=headers, method="POST")

        assert r.json().get("user_data"), "User data missing"
        return r.json()

    def contracts(self, user=None, status=["ASSIGNED", "PENDING"]):
        if user is None:
            user = self._return_token_field("name")
        if isinstance(status, str):
            status = [status]

        path = "/ofex-contracts-api/contracts"
        query = {"lang": "ca", "userId": user, "clientId": user}
        for idx, stat in enumerate(status):
            query[f"assignationStatus[{str(idx)}]"] = stat.upper()

        r = self._query(path, query)

        data = r.json().get("data")
        return data

    @property
    def contract_id(self):
        return [x["contractDetail"]["contractNumber"] for x in self.contracts()]

    @property
    def first_contract(self):
        contract_ids = self.contract_id
        assert (
            len(contract_ids) == 1
        ), "Provide a Contract ID to retrieve specific invoices"
        return contract_ids[0]

    def invoices(self, contract=None, user=None, last_months=36, mode="ALL"):
        if user is None:
            user = self._return_token_field("name")
        if contract is None:
            contract = self.first_contract

        path = "/ofex-invoices-api/invoices"
        query = {
            "contractNumber": contract,
            "userId": user,
            "clientId": user,
            "lang": "ca",
            "lastMonths": last_months,
            "mode": mode,
        }

        r = self._query(path, query)

        data = r.json().get("data")
        return data

    def invoices_debt(self, contract=None, user=None):
        return self.invoices(contract, user, last_months=0, mode="DEBT")

    def consumptions(
        self, date_from, date_to=None, contract=None, user=None, frequency="HOURLY"
    ):
        if self.provider == "sorea":
            import requests
            if date_to is None:
                date_to = date_from + datetime.timedelta(days=1)
            if isinstance(date_from, datetime.date):
                date_from = date_from.strftime("%d/%m/%Y")
            if isinstance(date_to, datetime.date):
                date_to = date_to.strftime("%d/%m/%Y")
            url = f"{self.api_host}/es/group/soreaonline/mis-consumos"
            params = {
                "p_p_id": "MisConsumos",
                "p_p_lifecycle": "2",
                "p_p_state": "normal",
                "p_p_mode": "view",
                "p_p_cacheability": "cacheLevelPage",
                "_MisConsumos_op": "buscarConsumosHoraria",
                "_MisConsumos_fechaInicio": date_from,
                "_MisConsumos_fechaFin": date_to,
                "_MisConsumos_inicio": "0",
                "_MisConsumos_fin": "9",
            }
            headers = {
                "User-Agent": self.headers["User-Agent"],
                "Accept": "*/*",
                "X-Requested-With": "XMLHttpRequest",
            }
            cookies = {}
            if self._cookie:
                cookies["JSESSIONID"] = self._cookie
            resp = requests.get(url, params=params, headers=headers, cookies=cookies, timeout=TIMEOUT)
            self.last_response = resp.text
            try:
                json_resp = resp.json()
                consumos = json_resp.get("consumos", [])
                # Normalizar formato: convertir coma decimal a punto y fechas a ISO
                result = []
                for c in consumos:
                    lectura = float(str(c.get("lectura", "0")).replace(",", "."))
                    consumo = float(str(c.get("consumo", "0")).replace(",", "."))
                    fecha = c.get("fechaConsumo", "")
                    hora = c.get("horaConsumo", "")
                    # Convertir fecha y hora a ISO
                    try:
                        dt = datetime.datetime.strptime(f"{fecha} {hora}", "%d %b %Y %H:%M")
                        dt_iso = dt.isoformat()
                    except Exception:
                        dt_iso = f"{fecha} {hora}"
                    result.append({
                        "accumulatedConsumption": lectura,
                        "consumption": consumo,
                        "datetime": dt_iso,
                        "raw": c
                    })
                return result
            except Exception:
                return None
        # ...existing code...
    def contracts(self, user=None, status=["ASSIGNED", "PENDING"]):
        if self.provider == "sorea":
            import requests
            url = f"{self.api_host}/es/group/soreaonline/mis-contratos"
            params = {
                "p_p_id": "ContractDetails",
                "p_p_lifecycle": "2",
                "p_p_state": "normal",
                "p_p_mode": "view",
                "p_p_cacheability": "cacheLevelPage",
                "_ContractDetails_op": "loadContratos",
                "_ContractDetails_offset": "0",
                "_ContractDetails_limit": "10",
            }
            headers = {
                "User-Agent": self.headers["User-Agent"],
                "Accept": "*/*",
                "X-Requested-With": "XMLHttpRequest",
            }
            cookies = {}
            if self._cookie:
                cookies["JSESSIONID"] = self._cookie
            resp = requests.get(url, params=params, headers=headers, cookies=cookies, timeout=TIMEOUT)
            self.last_response = resp.text
            try:
                json_resp = resp.json()
                return json_resp.get("contractToShow", [])
            except Exception:
                return None
        # ...existing code...
    def invoices(self, contract=None, user=None, last_months=36, mode="ALL"):
        if self.provider == "sorea":
            import requests
            url = f"{self.api_host}/es/group/soreaonline/mis-facturas"
            params = {
                "p_p_id": "MisFacturas",
                "p_p_lifecycle": "2",
                "p_p_state": "normal",
                "p_p_mode": "view",
                "p_p_cacheability": "cacheLevelPage",
                "_MisFacturas_op": "loadFacturas",
                "_MisFacturas_numeroContrato": contract or "",
                "_MisFacturas_inicio": "0",
                "_MisFacturas_fin": str(last_months-1),
                "_MisFacturas_numeroFacturaBusqueda": "",
                "_MisFacturas_estadoBusqueda": "",
                "_MisFacturas_fechaEmisionDesdeBusqueda": "",
                "_MisFacturas_fechaEmisionHastaBusqueda": "",
                "_MisFacturas_importeDesdeBusqueda": "",
                "_MisFacturas_importeHastaBusqueda": "",
                "_MisFacturas_12-gotas": "false",
            }
            headers = {
                "User-Agent": self.headers["User-Agent"],
                "Accept": "*/*",
                "X-Requested-With": "XMLHttpRequest",
            }
            cookies = {}
            if self._cookie:
                cookies["JSESSIONID"] = self._cookie
            resp = requests.get(url, params=params, headers=headers, cookies=cookies, timeout=TIMEOUT)
            self.last_response = resp.text
            try:
                json_resp = resp.json()
                return json_resp.get("facturas", [])
            except Exception:
                return None
        # ...existing code...
    def profile(self, user=None):
        if self.provider == "sorea":
            # No disponible para Sorea
            return None
        # ...existing code...

    def consumptions_week(self, date_from: datetime.date, contract=None, user=None):
        if date_from is None:
            date_from = datetime.datetime.now()
        # get first day of week
        monday = date_from - datetime.timedelta(days=date_from.weekday())
        sunday = monday + datetime.timedelta(days=6)
        return self.consumptions(monday, sunday, contract, user, frequency="DAILY")

    def consumptions_month(self, date_from: datetime.date, contract=None, user=None):
        first = date_from.replace(day=1)
        next_month = date_from.replace(day=28) + datetime.timedelta(days=4)
        last = next_month - datetime.timedelta(days=next_month.day)
        return self.consumptions(first, last, contract, user, frequency="DAILY")

    def parse_consumptions(self, info, key="accumulatedConsumption"):
        return [x[key] for x in info]
