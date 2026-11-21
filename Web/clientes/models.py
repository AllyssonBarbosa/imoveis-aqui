from django.core.exceptions import ValidationError
from django.db import models

from core.models import EmpresaOwnedModel
from core.validators import somente_digitos, validar_cnpj, validar_cpf


class Cliente(EmpresaOwnedModel):
    """Quem compra ou aluga o imóvel (W12). É só um cadastro da empresa.

    Não tem acesso ao sistema: não há ligação nenhuma com Usuario, então não
    existe login de cliente. Quem consulta o contrato e as parcelas é a
    imobiliária. O cliente também nunca aparece na vitrine (site ou API pública).
    """

    class TipoPessoa(models.TextChoices):
        FISICA = "FISICA", "Pessoa física"
        JURIDICA = "JURIDICA", "Pessoa jurídica"

    tipo_pessoa = models.CharField("tipo de pessoa", max_length=10, choices=TipoPessoa.choices)
    nome_razao_social = models.CharField("nome ou razão social", max_length=200)
    documento = models.CharField("CPF ou CNPJ", max_length=18)
    telefone = models.CharField(max_length=20, blank=True)
    email = models.EmailField("e-mail", blank=True)
    observacoes = models.TextField("observações", blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "cliente"
        verbose_name_plural = "clientes"
        ordering = ["nome_razao_social", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["empresa", "documento"],
                name="cliente_documento_unico_por_empresa",
                violation_error_message="Já existe um cliente com esse documento nesta empresa.",
            )
        ]

    def __str__(self):
        return self.nome_razao_social

    def clean(self):
        super().clean()
        # guarda só os dígitos: "123.456.789-09" e "12345678909" são o mesmo CPF
        if self.documento:
            self.documento = somente_digitos(self.documento)

        # mesma validação de CPF/CNPJ do proprietário (dígitos verificadores, do core)
        if self.tipo_pessoa == self.TipoPessoa.FISICA:
            if not validar_cpf(self.documento):
                raise ValidationError({"documento": "CPF inválido."})
        elif self.tipo_pessoa == self.TipoPessoa.JURIDICA:
            if not validar_cnpj(self.documento):
                raise ValidationError({"documento": "CNPJ inválido."})

        if not (self.telefone or "").strip() and not (self.email or "").strip():
            raise ValidationError("Informe pelo menos um contato do cliente: telefone ou e-mail.")

    @property
    def documento_formatado(self):
        d = self.documento
        if self.tipo_pessoa == self.TipoPessoa.FISICA and len(d) == 11:
            return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
        if self.tipo_pessoa == self.TipoPessoa.JURIDICA and len(d) == 14:
            return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
        return d
