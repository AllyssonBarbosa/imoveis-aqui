from django.db import models


class Caracteristica(models.Model):
    """Tabela geral mantida pelo administrador (portão eletrônico, ar-condicionado etc.).

    Marcável em cada imóvel — o Imóvel (E2) vai ter um ManyToMany pra cá,
    o que permite filtrar a vitrine por característica em vez de vasculhar
    o texto da descrição.
    """

    nome = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name = "característica"
        verbose_name_plural = "características"
        ordering = ["nome"]

    def __str__(self):
        return self.nome
