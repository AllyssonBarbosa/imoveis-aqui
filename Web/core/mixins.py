class EmpresaScopedQuerySetMixin:
    """Filtra o queryset da ViewSet pela empresa do usuário logado.

    Todo ViewSet que expõe dado do acervo (herdeiro de EmpresaOwnedModel)
    deve usar este mixin. Como o queryset já vem filtrado, tentar acessar
    um registro de outra empresa pelo ID na URL cai no fluxo normal de
    "não encontrado" (404) em vez de vazar que o registro existe.
    """

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(empresa=self.request.user.empresa)

    def perform_create(self, serializer):
        serializer.save(empresa=self.request.user.empresa)
