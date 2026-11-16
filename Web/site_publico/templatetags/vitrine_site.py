from django import template

from empresas.models import Empresa
from imoveis.models import Apartamento, Imovel, Terreno

register = template.Library()

# os serializers públicos devolvem o código da opção ("CASA", "PLANO"...);
# o texto exibido vem das choices dos models, sem repetir a lista aqui
ROTULOS = {
    "natureza": dict(Imovel.Natureza.choices),
    "finalidade": dict(Imovel.Finalidade.choices),
    "topografia": dict(Terreno.Topografia.choices),
    "uso_permitido": dict(Terreno.UsoPermitido.choices),
    "posicao_solar": dict(Apartamento.PosicaoSolar.choices),
    "tipo_empresa": dict(Empresa.Tipo.choices),
}


@register.filter
def rotulo(valor, campo):
    """{{ imovel.natureza|rotulo:"natureza" }} → "Casa"."""
    return ROTULOS[campo].get(valor, valor)
