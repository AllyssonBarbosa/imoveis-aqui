from django.core.exceptions import ValidationError
from django.db import models

from core.validators import somente_digitos, validar_cnpj, validar_cpf, validar_imagem


class Empresa(models.Model):
    """Imobiliária ou corretor autônomo — os dois entram nesta mesma tabela.

    No caso do autônomo, a mesma pessoa é a Empresa (com CPF) e também
    o Usuario de perfil corretor vinculado a ela.
    """

    class Tipo(models.TextChoices):
        IMOBILIARIA = "IMOBILIARIA", "Imobiliária"
        AUTONOMO = "AUTONOMO", "Corretor autônomo"

    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    razao_social_ou_nome = models.CharField("razão social ou nome", max_length=200)
    cnpj = models.CharField(max_length=18, unique=True, null=True, blank=True)
    cpf = models.CharField(max_length=14, unique=True, null=True, blank=True)
    creci = models.CharField("CRECI", max_length=20)
    logomarca = models.ImageField(
        upload_to="empresas/logomarcas/", null=True, blank=True, validators=[validar_imagem]
    )
    descricao = models.TextField(blank=True)
    telefone = models.CharField(max_length=20, blank=True)
    whatsapp = models.CharField(max_length=20)
    email = models.EmailField()
    endereco = models.OneToOneField(
        "core.Endereco", on_delete=models.PROTECT, null=True, blank=True, related_name="empresa"
    )
    cidades_atuacao = models.ManyToManyField(
        "localizacao.Cidade", related_name="empresas_atuantes", blank=True
    )
    ativa = models.BooleanField(default=True)
    criada_em = models.DateTimeField(auto_now_add=True)
    atualizada_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "empresa"
        verbose_name_plural = "empresas"
        ordering = ["razao_social_ou_nome"]

    def __str__(self):
        return self.razao_social_ou_nome

    def clean(self):
        super().clean()
        if self.cnpj:
            self.cnpj = somente_digitos(self.cnpj)
        if self.cpf:
            self.cpf = somente_digitos(self.cpf)

        if self.tipo == self.Tipo.IMOBILIARIA:
            if not self.cnpj:
                raise ValidationError({"cnpj": "Imobiliária precisa de CNPJ."})
            if self.cpf:
                raise ValidationError({"cpf": "Imobiliária não deve ter CPF."})
            if not validar_cnpj(self.cnpj):
                raise ValidationError({"cnpj": "CNPJ inválido."})
        elif self.tipo == self.Tipo.AUTONOMO:
            if not self.cpf:
                raise ValidationError({"cpf": "Corretor autônomo precisa de CPF."})
            if self.cnpj:
                raise ValidationError({"cnpj": "Corretor autônomo não deve ter CNPJ."})
            if not validar_cpf(self.cpf):
                raise ValidationError({"cpf": "CPF inválido."})
