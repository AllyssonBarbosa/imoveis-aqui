from django import forms

from core.models import Endereco

from .models import Casa, Imovel, Terreno


class ImovelForm(forms.ModelForm):
    class Meta:
        model = Imovel
        fields = [
            "codigo", "titulo", "natureza", "corretor_responsavel", "proprietario", "finalidade",
            "preco_venda", "preco_aluguel", "preco_condominio", "preco_iptu",
            "descricao", "caracteristicas",
        ]
        widgets = {
            "caracteristicas": forms.CheckboxSelectMultiple,
            "descricao": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        if empresa is not None:
            from contas.models import Usuario

            self.fields["corretor_responsavel"].queryset = Usuario.objects.filter(
                empresa=empresa, perfil=Usuario.Perfil.CORRETOR
            )
            from proprietarios.models import Proprietario

            self.fields["proprietario"].queryset = Proprietario.objects.filter(empresa=empresa)


class EnderecoForm(forms.ModelForm):
    class Meta:
        model = Endereco
        fields = [
            "cep", "logradouro", "numero", "complemento", "bairro", "cidade",
            "latitude", "longitude",
        ]


class CasaForm(forms.ModelForm):
    # área livre fica de fora de propósito: é calculada (Casa.area_livre), nunca digitada
    class Meta:
        model = Casa
        fields = [
            "area_terreno", "area_construida", "quartos", "suites", "banheiros", "salas",
            "vagas", "pavimentos", "ano_construcao", "piscina", "quintal", "churrasqueira",
            "mobiliada",
        ]


class TerrenoForm(forms.ModelForm):
    class Meta:
        model = Terreno
        fields = [
            "area_total", "frente", "fundo", "lateral_esquerda", "lateral_direita",
            "topografia", "murado", "esquina", "uso_permitido",
        ]


FORM_POR_NATUREZA = {
    Imovel.Natureza.CASA: (CasaForm, Casa),
    Imovel.Natureza.TERRENO: (TerrenoForm, Terreno),
}


def form_de_detalhes(imovel, data=None):
    """Formulário só com os campos da natureza do imóvel (casa ou terreno)."""
    form_class, model = FORM_POR_NATUREZA[imovel.natureza]
    instancia = imovel.detalhes or model(imovel=imovel)
    return form_class(data, instance=instancia)
