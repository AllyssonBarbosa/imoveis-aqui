from django import forms

from contas.models import Usuario
from imoveis.models import Imovel

from .models import Baixa, Contrato, DocumentoContrato


def campo_de_data(rotulo, obrigatorio=True, ajuda=""):
    """Data em dd/mm/aaaa, com a máscara do painel (static/js/mascaras.js), igual à W08.

    O <input type="date"> segue o idioma do navegador (num Chrome em inglês
    aparece mm/dd/yyyy), então não dá pra garantir o formato com ele.
    """
    return forms.DateField(
        label=rotulo,
        required=obrigatorio,
        help_text=ajuda,
        input_formats=["%d/%m/%Y"],
        widget=forms.DateInput(
            format="%d/%m/%Y",
            attrs={
                "placeholder": "dd/mm/aaaa", "inputmode": "numeric", "maxlength": "10",
                "data-mascara": "data",
            },
        ),
        error_messages={"invalid": "Informe a data no formato dd/mm/aaaa (ex.: 15/01/2027)."},
    )


class ContratoForm(forms.ModelForm):
    """Cadastro e edição do contrato pelo gestor.

    As listas de imóvel, cliente e corretor só trazem o que é da empresa do
    gestor. Depois de assinado, os campos do negócio aparecem desabilitados:
    o Django ignora o que vier neles no POST (e o servidor recusa de novo
    em salvar_contrato, por garantia).
    """

    data_inicio = campo_de_data(
        "Data de início",
        ajuda="Venda: data do negócio. Locação: início da vigência. A 1ª parcela vence no mês seguinte.",
    )
    data_fim = campo_de_data(
        "Fim da vigência", obrigatorio=False,
        ajuda="Só na locação. Termina um dia antes do mesmo dia do início (ex.: 01/01/2027 a 31/12/2027).",
    )

    class Meta:
        model = Contrato
        fields = [
            "tipo", "imovel", "cliente", "corretor", "valor", "entrada", "numero_parcelas",
            "dia_vencimento", "data_inicio", "data_fim", "observacoes",
        ]
        widgets = {"observacoes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        from clientes.models import Cliente

        self.fields["imovel"].queryset = Imovel.objects.filter(empresa=empresa).order_by("codigo")
        self.fields["cliente"].queryset = Cliente.objects.filter(empresa=empresa)
        corretores = Usuario.objects.filter(empresa=empresa, perfil=Usuario.Perfil.CORRETOR)
        if self.instance.pk:
            # na edição, mantém o corretor atual na lista mesmo que tenha sido desativado
            corretores = corretores.filter(is_active=True) | corretores.filter(pk=self.instance.corretor_id)
        else:
            corretores = corretores.filter(is_active=True)
        self.fields["corretor"].queryset = corretores.order_by("nome")
        self.fields["entrada"].required = False

        if self.instance.pk and not self.instance.pode_alterar_negocio():
            for campo in Contrato.CAMPOS_DO_NEGOCIO:
                self.fields[campo].disabled = True

    def clean_entrada(self):
        # em branco = sem entrada (o caso de toda locação)
        return self.cleaned_data.get("entrada") or 0


class FiltroContratoForm(forms.Form):
    tipo = forms.ChoiceField(label="Tipo", required=False, choices=[("", "Todos")] + Contrato.Tipo.choices)
    situacao = forms.ChoiceField(
        label="Situação", required=False, choices=[("", "Todas")] + Contrato.Situacao.choices
    )

    def filtrar(self, queryset):
        if not self.is_valid():
            return queryset
        if self.cleaned_data["tipo"]:
            queryset = queryset.filter(tipo=self.cleaned_data["tipo"])
        if self.cleaned_data["situacao"]:
            queryset = queryset.filter(situacao=self.cleaned_data["situacao"])
        return queryset


class AssinaturaForm(forms.Form):
    data_assinatura = campo_de_data(
        "Data da assinatura", ajuda="Ao assinar, o imóvel sai da vitrine como vendido ou alugado.",
    )


class DocumentoContratoForm(forms.ModelForm):
    class Meta:
        model = DocumentoContrato
        fields = ["tipo", "descricao", "arquivo"]
        help_texts = {"arquivo": "PDF, JPG ou PNG de até 10MB."}


class BaixaForm(forms.ModelForm):
    data_pagamento = campo_de_data("Data do pagamento")

    class Meta:
        model = Baixa
        fields = ["data_pagamento", "valor_pago", "forma", "comprovante", "observacao"]
        help_texts = {
            "valor_pago": "Pode ser parcial ou a mais. A parcela fica paga quando a soma cobre o valor.",
            "comprovante": "PDF, JPG ou PNG de até 10MB. Obrigatório.",
        }
