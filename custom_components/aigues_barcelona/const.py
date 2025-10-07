"""Constants definition."""


DOMAIN = "aigues_barcelona"

CONF_CONTRACT = "contract"
CONF_VALUE = "value"
CONF_PROVIDER = "provider"

ATTR_LAST_MEASURE = "Last measure"

DEFAULT_SCAN_PERIOD = 14400

API_HOSTS = {
	"agbar": "api.aiguesdebarcelona.cat",
	"sorea": "api.soreaonline.cat",  # Confirmar este dominio con datos reales
}
API_COOKIE_TOKEN = "ofexTokenJwt"

API_ERROR_TOKEN_REVOKED = "JWT Token Revoked"
