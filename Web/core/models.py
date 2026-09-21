from django.db import models


class Endereco(models.Model):
    """Endereço reaproveitável — usado pela Empresa e pelo Imóvel.

    Latitude/longitude ficam opcionais: o endereço pode existir sem
    coordenada ainda (rascunho), mas o Imóvel não pode ser publicado
    sem as duas — ver Imovel.pode_publicar().
    """

    logradouro = models.CharField(max_length=200)
    numero = models.CharField(max_length=20, blank=True)
    complemento = models.CharField(max_length=100, blank=True)
    bairro = models.CharField(max_length=100, blank=True)
    cep = models.CharField("CEP", max_length=9, blank=True)
    cidade = models.ForeignKey(
        "localizacao.Cidade", on_delete=models.PROTECT, related_name="enderecos"
    )
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    class Meta:
        verbose_name = "endereço"
        verbose_name_plural = "endereços"

    def __str__(self):
        return f"{self.logradouro}, {self.numero} - {self.cidade}"


class EmpresaOwnedModel(models.Model):
    """Base para toda tabela do acervo: garante a coluna empresa e o isolamento multitenant.

    Nenhum modelo de dado do acervo (imóvel, contrato etc.) deve ser criado
    sem herdar daqui — é a regra que não pode ser quebrada no projeto.
    """

    empresa = models.ForeignKey(
        "empresas.Empresa", on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        abstract = True
