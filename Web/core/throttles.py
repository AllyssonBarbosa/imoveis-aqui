from rest_framework.throttling import AnonRateThrottle


class ThrottleVitrinePublica(AnonRateThrottle):
    """Limite por IP das rotas públicas da vitrine (sem token).

    Entra view a view, nunca como padrão global do REST_FRAMEWORK, para a API
    autenticada do painel não ser limitada. As rotas públicas dividem um só balde
    por IP de cliente (escopo vitrine_publica); a taxa vem de
    REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] (variável VITRINE_TAXA_ANONIMA).
    """

    scope = "vitrine_publica"


class ThrottleContatoPublico(ThrottleVitrinePublica):
    """Limite por IP do POST público de contato (sem token).

    O POST grava um registro na caixa do corretor, então tem balde e taxa próprios
    (escopo contato_publico, variável CONTATO_TAXA_ANONIMA): navegar na vitrine não
    consome esta cota e esgotá-la não bloqueia a vitrine. Passou do limite, responde
    429 com Retry-After antes de a view gravar qualquer coisa.
    """

    scope = "contato_publico"
