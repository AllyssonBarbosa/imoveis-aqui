import hashlib

from rest_framework.throttling import AnonRateThrottle, SimpleRateThrottle


class ThrottleVitrinePublica(AnonRateThrottle):
    """Limite por IP das rotas públicas da vitrine (sem token).

    Entra view a view, nunca como padrão global do REST_FRAMEWORK, para a API
    autenticada do painel não ser limitada. As rotas públicas dividem um só balde
    por IP de cliente (escopo vitrine_publica); a taxa vem de
    REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] (variável VITRINE_TAXA_ANONIMA).
    """

    scope = "vitrine_publica"


class ThrottleLoginPorEmail(SimpleRateThrottle):
    """Limite de tentativas de login por e-mail (escopo login_email).

    Roda antes do `authenticate()` (o DRF checa os throttles em `initial()`) e conta toda
    tentativa, com senha certa ou errada, para existir ou não o e-mail: a resposta do limite
    é idêntica e não revela se a conta existe. O e-mail normalizado (sem espaços e em
    minúsculas) só entra na chave de cache como hash SHA-256, nunca em texto. Sem e-mail no
    corpo não há chave (None) e quem responde é o 400 do serializer. A taxa vem de
    LOGIN_TAXA_POR_EMAIL.
    """

    scope = "login_email"

    def get_cache_key(self, request, view):
        dados = request.data
        if not hasattr(dados, "get"):
            return None
        email = str(dados.get("email") or "").strip().lower()
        if not email:
            return None
        identificador = hashlib.sha256(email.encode("utf-8")).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": identificador}


class ThrottleLoginPorIp(SimpleRateThrottle):
    """Limite de tentativas de login por IP do cliente (escopo login_ip).

    Freia quem testa muitos e-mails do mesmo lugar. Usa `get_ident`, que respeita NUM_PROXIES
    (0 ignora o X-Forwarded-For, que o cliente forja). Também roda antes do `authenticate()`
    e conta toda tentativa. A taxa vem de LOGIN_TAXA_POR_IP.
    """

    scope = "login_ip"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}
