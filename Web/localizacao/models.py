from django.db import models


class Cidade(models.Model):
    """Tabela geral mantida pelo administrador (usada na vitrine por cidade)."""

    nome = models.CharField(max_length=100)
    uf = models.CharField("UF", max_length=2)

    class Meta:
        verbose_name = "cidade"
        verbose_name_plural = "cidades"
        constraints = [
            models.UniqueConstraint(fields=["nome", "uf"], name="cidade_unica_por_uf"),
        ]
        ordering = ["nome", "uf"]

    def __str__(self):
        return f"{self.nome}/{self.uf}"
