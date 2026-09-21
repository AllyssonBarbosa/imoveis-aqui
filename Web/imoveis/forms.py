from django import forms

from core.models import Endereco

from .models import Imovel


class ImovelForm(forms.ModelForm):
    class Meta:
        model = Imovel
        fields = [
            "codigo", "titulo", "corretor_responsavel", "proprietario", "finalidade",
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
