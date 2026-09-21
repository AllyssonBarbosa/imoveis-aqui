from django.core.exceptions import ValidationError
from django.db import models

from core.models import EmpresaOwnedModel
from core.validators import somente_digitos, validar_cnpj, validar_cpf


class Proprietario(EmpresaOwnedModel):
    """Dono do imóvel. Não tem acesso ao sistema — é só um cadastro da empresa."""

    class TipoPessoa(models.TextChoices):
        FISICA = "FISICA", "Pessoa física"
        JURIDICA = "JURIDICA", "Pessoa jurídica"

    tipo_pessoa = models.CharField(max_length=10, choices=TipoPessoa.choices)
    nome_razao_social = models.CharField("nome ou razão social", max_length=200)
    documento = models.CharField("CPF ou CNPJ", max_length=18)
    telefone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    observacoes = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "proprietário"
        verbose_name_plural = "proprietários"
        ordering = ["nome_razao_social"]
        constraints = [
            models.UniqueConstraint(
                fields=["empresa", "documento"],
                name="documento_unico_por_empresa",
                violation_error_message="Já existe um proprietário com esse documento nesta empresa.",
            )
        ]

    def __str__(self):
        return self.nome_razao_social

    def clean(self):
        super().clean()
        if self.documento:
            self.documento = somente_digitos(self.documento)

        if self.tipo_pessoa == self.TipoPessoa.FISICA:
            if not validar_cpf(self.documento):
                raise ValidationError({"documento": "CPF inválido."})
        elif self.tipo_pessoa == self.TipoPessoa.JURIDICA:
            if not validar_cnpj(self.documento):
                raise ValidationError({"documento": "CNPJ inválido."})

    @property
    def documento_formatado(self):
        d = self.documento
        if self.tipo_pessoa == self.TipoPessoa.FISICA and len(d) == 11:
            return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
        if self.tipo_pessoa == self.TipoPessoa.JURIDICA and len(d) == 14:
            return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
        return d
