from django import forms

from core.models import Endereco

from .models import Apartamento, Casa, Imovel, Predio, Terreno


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


class ApartamentoForm(forms.ModelForm):
    # endereço, lazer e portaria não aparecem aqui: vêm do prédio escolhido
    class Meta:
        model = Apartamento
        fields = [
            "predio", "andar", "numero", "area_privativa", "area_total", "quartos", "suites",
            "banheiros", "vagas", "varanda", "posicao_solar",
        ]

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        if empresa is not None:
            self.fields["predio"].queryset = Predio.objects.filter(empresa=empresa)


class PredioForm(forms.ModelForm):
    class Meta:
        model = Predio
        fields = [
            "nome", "andares", "unidades_por_andar", "ano_construcao", "elevadores",
            "portaria_24h", "piscina", "academia", "salao_de_festas", "playground",
            "valor_condominio_base",
        ]


FORM_POR_NATUREZA = {
    Imovel.Natureza.CASA: (CasaForm, Casa),
    Imovel.Natureza.TERRENO: (TerrenoForm, Terreno),
    Imovel.Natureza.APARTAMENTO: (ApartamentoForm, Apartamento),
}


def form_de_detalhes(imovel, data=None):
    """Formulário só com os campos da natureza do imóvel (casa, terreno ou apartamento)."""
    form_class, model = FORM_POR_NATUREZA[imovel.natureza]
    # imovel_id, e não imovel=: atribuir o objeto guardaria a instância vazia no cache
    # de imovel.casa/terreno/apartamento, e a tela acharia que os dados já existem
    instancia = imovel.detalhes or model(imovel_id=imovel.pk)
    if form_class is ApartamentoForm:
        return form_class(data, instance=instancia, empresa=imovel.empresa)
    return form_class(data, instance=instancia)
