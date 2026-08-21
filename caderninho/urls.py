from django.urls import path
from . import views

urlpatterns = [
    # Rotas Públicas do Cliente
    path('', views.identificacao_cliente, name='identificacao_cliente'),
    path('sair/', views.deslogar_cliente, name='deslogar_cliente'),
    path('cardapio/', views.cardapio, name='cardapio'),
    path('pedido-confirmado/<int:pedido_id>/', views.pedido_sucesso, name='pedido_sucesso'),
    path('meus-pedidos/', views.cliente_historico, name='cliente_historico'),

    # Rotas do Admin Customizado (Protegidas por staff_member_required)
    path('painel/login/', views.admin_login, name='admin_login'),
    path('painel/', views.admin_dashboard, name='admin_dashboard'),
    
    # Gestão de Produtos
    path('painel/produtos/', views.admin_produtos, name='admin_produtos'),
    path('painel/produtos/novo/', views.admin_produto_criar, name='admin_produto_criar'),
    path('painel/produtos/editar/<int:produto_id>/', views.admin_produto_editar, name='admin_produto_editar'),
    path('painel/produtos/toggle-dispo/<int:produto_id>/', views.admin_produto_toggle, name='admin_produto_toggle'),
    
    # Gestão de Pedidos/Vendas
    path('painel/pedidos/', views.admin_pedidos, name='admin_pedidos'),
    path('painel/pedidos/novo/', views.admin_pedido_criar, name='admin_pedido_criar'),
    path('painel/pedidos/status/<int:pedido_id>/<str:novo_status>/', views.admin_pedido_alterar_status, name='admin_pedido_alterar_status'),
    path('painel/pedidos/financeiro/<int:pedido_id>/<str:novo_financeiro>/', views.admin_pedido_alterar_financeiro, name='admin_pedido_alterar_financeiro'),

    # Gestão de Clientes
    path('painel/clientes/', views.admin_clientes, name='admin_clientes'),
    path('painel/clientes/novo/', views.admin_cliente_criar, name='admin_cliente_criar'),
    path('painel/clientes/editar/<int:cliente_id>/', views.admin_cliente_editar, name='admin_cliente_editar'),
    path('painel/clientes/<int:cliente_id>/', views.admin_cliente_detalhes, name='admin_cliente_detalhes'),
    path('painel/clientes/<int:cliente_id>/fechar-mes/', views.admin_cliente_fechar_mes, name='admin_cliente_fechar_mes'),
    path('painel/clientes/<int:cliente_id>/reset-senha/', views.admin_cliente_reset_senha, name='admin_cliente_reset_senha'),
    
    # Gestão de Fechamentos
    path('painel/fechamentos/<int:fechamento_id>/pagar/', views.admin_fechamento_pagar, name='admin_fechamento_pagar'),
]
