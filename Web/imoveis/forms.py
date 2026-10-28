from django import forms
from django.db import models

from core.models import Endereco

from .models import (
    Apartamento,
    Casa,
    DocumentoImovel,
    Imovel,
    Lote,
    Loteamento,
    Predio,
    Quadra,
    Terreno,
)


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


class LoteForm(forms.ModelForm):
    # o preço é o preço de venda do imóvel; infraestrutura e lazer vêm do loteamento
    class Meta:
        model = Lote
        fields = [
            "quadra", "numero", "area", "frente", "fundo", "entrada_minima",
            "parcelas_maximas", "condicoes_parcelamento",
        ]
        widgets = {"condicoes_parcelamento": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        if empresa is not None:
            self.fields["quadra"].queryset = Quadra.objects.filter(
                loteamento__empresa=empresa
            ).select_related("loteamento")


class LoteamentoForm(forms.ModelForm):
    class Meta:
        model = Loteamento
        fields = [
            "nome", "cidade", "incorporadora", "asfalto", "agua", "energia", "esgoto",
            "iluminacao", "areas_de_lazer",
        ]
        widgets = {"areas_de_lazer": forms.Textarea(attrs={"rows": 3})}


class QuadraForm(forms.ModelForm):
    class Meta:
        model = Quadra
        fields = ["identificacao"]


FORM_POR_NATUREZA = {
    Imovel.Natureza.CASA: (CasaForm, Casa),
    Imovel.Natureza.TERRENO: (TerrenoForm, Terreno),
    Imovel.Natureza.APARTAMENTO: (ApartamentoForm, Apartamento),
    Imovel.Natureza.LOTE: (LoteForm, Lote),
}


def form_de_detalhes(imovel, data=None):
    """Formulário só com os campos da natureza do imóvel (casa, terreno, apartamento ou lote)."""
    form_class, model = FORM_POR_NATUREZA[imovel.natureza]
    # imovel_id, e não imovel=: atribuir o objeto guardaria a instância vazia no cache
    # de imovel.casa/terreno/apartamento, e a tela acharia que os dados já existem
    instancia = imovel.detalhes or model(imovel_id=imovel.pk)
    if form_class in (ApartamentoForm, LoteForm):
        return form_class(data, instance=instancia, empresa=imovel.empresa)
    return form_class(data, instance=instancia)


class SituacaoForm(forms.Form):
    situacao = forms.ChoiceField(label="Nova situação", choices=Imovel.Situacao.choices)
    observacao = forms.CharField(
        label="Observação", required=False, widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Ex.: reservado para o cliente João até a assinatura.",
    )


class DocumentoImovelForm(forms.ModelForm):
    class Meta:
        model = DocumentoImovel
        fields = ["tipo", "arquivo", "data_documento"]
        widgets = {"data_documento": forms.DateInput(attrs={"type": "date"})}


class FiltroImovelForm(forms.Form):
    """Filtros das listas de imóveis (corretor e gestor), lidos da query string.

    Cada lista escolhe quais filtros mostra (`campos`). A regra de cada filtro
    fica aqui, num lugar só — "na vitrine" usa a mesma consulta da vitrine pública.
    """

    class Vitrine:
        NA_VITRINE = "NA_VITRINE"
        PUBLICADO_FORA = "PUBLICADO_FORA"
        NAO_PUBLICADO = "NAO_PUBLICADO"
        CHOICES = [
            (NA_VITRINE, "Na vitrine"),
            (PUBLICADO_FORA, "Publicado, mas fora da vitrine"),
            (NAO_PUBLICADO, "Não publicado"),
        ]

    busca = forms.CharField(
        label="Busca", required=False,
        widget=forms.TextInput(attrs={"placeholder": "Código ou título"}),
    )
    natureza = forms.ChoiceField(
        label="Natureza", required=False, choices=[("", "Todas")] + Imovel.Natureza.choices
    )
    finalidade = forms.ChoiceField(
        label="Finalidade", required=False, choices=[("", "Todas")] + Imovel.Finalidade.choices
    )
    corretor = forms.ModelChoiceField(
        label="Corretor", required=False, queryset=None, empty_label="Todos"
    )
    situacao = forms.ChoiceField(
        label="Situação", required=False, choices=[("", "Todas")] + Imovel.Situacao.choices
    )
    vitrine = forms.ChoiceField(
        label="Vitrine", required=False, choices=[("", "Todas")] + Vitrine.CHOICES
    )

    def __init__(self, *args, empresa, campos, **kwargs):
        super().__init__(*args, **kwargs)
        from contas.models import Usuario

        self.fields["corretor"].queryset = Usuario.objects.filter(
            empresa=empresa, perfil=Usuario.Perfil.CORRETOR
        ).order_by("nome")
        for nome in list(self.fields):
            if nome not in campos:
                del self.fields[nome]

    @property
    def tem_filtro(self):
        return self.is_valid() and any(self.cleaned_data.values())

    def filtrar(self, queryset):
        # filtro inválido (ex.: corretor de outra empresa na URL) é ignorado, não quebra a lista
        if not self.is_valid():
            return queryset
        dados = self.cleaned_data

        if dados.get("busca"):
            queryset = queryset.filter(
                models.Q(codigo__icontains=dados["busca"]) | models.Q(titulo__icontains=dados["busca"])
            )
        for campo in ("natureza", "finalidade", "situacao"):
            if dados.get(campo):
                queryset = queryset.filter(**{campo: dados[campo]})
        if dados.get("corretor"):
            queryset = queryset.filter(corretor_responsavel=dados["corretor"])

        vitrine = dados.get("vitrine")
        na_vitrine = Imovel.objects.na_vitrine().values("pk")
        if vitrine == self.Vitrine.NA_VITRINE:
            queryset = queryset.filter(pk__in=na_vitrine)
        elif vitrine == self.Vitrine.PUBLICADO_FORA:
            queryset = queryset.filter(publicado=True).exclude(pk__in=na_vitrine)
        elif vitrine == self.Vitrine.NAO_PUBLICADO:
            queryset = queryset.filter(publicado=False)
        return queryset
