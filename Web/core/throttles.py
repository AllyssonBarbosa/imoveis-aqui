from rest_framework.throttling import AnonRateThrottle


class ThrottleVitrinePublica(AnonRateThrottle):
    """Limite por IP das rotas públicas da vitrine (sem token).

    Entra view a view, nunca como padrão global do REST_FRAMEWORK, para a API
    autenticada do painel não ser limitada. As rotas públicas dividem um só balde
    por IP de cliente (escopo vitrine_publica); a taxa vem de
    REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] (variável VITRINE_TAXA_ANONIMA).
    """

    scope = "vitrine_publica"
