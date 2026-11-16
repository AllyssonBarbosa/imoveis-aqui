from django.conf import settings
from django.core.checks import Tags, Warning, register


@register(Tags.security, deploy=True)
def verificar_num_proxies(app_configs, **kwargs):
    """Avisa quando a produção usa o NUM_PROXIES padrão (WR-08).

    Atrás de proxy reverso, `REMOTE_ADDR` é o IP do proxy para todo mundo: com NUM_PROXIES=0 o limite de
    taxa da vitrine vira um balde único e um cliente que passe da taxa bloqueia todos os outros com 429.
    É um aviso de `manage.py check --deploy` (e não um erro na partida) para não derrubar o desenvolvimento
    nem a suíte de testes, que rodam sem `.env` completo. Informar a variável, mesmo `0` (API exposta
    direto, sem proxy), cala o aviso.
    """
    if settings.DEBUG or getattr(settings, "NUM_PROXIES_CONFIGURADO", True):
        return []
    return [
        Warning(
            "NUM_PROXIES não foi informado fora do modo DEBUG (padrão 0).",
            hint=(
                "Atrás de proxy reverso, informe o número de proxies confiáveis (ex.: NUM_PROXIES=1); "
                "sem proxy, informe NUM_PROXIES=0 de propósito. Senão todos os visitantes dividem o "
                "limite de taxa do IP do proxy."
            ),
            id="core.W001",
        )
    ]
