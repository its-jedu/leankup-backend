from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'', views.TaskViewSet, basename='task')
router.register(r'applications', views.ApplicationViewSet, basename='application')
router.register(r'notifications', views.NotificationViewSet, basename='notification')

urlpatterns = [
    path('', include(router.urls)),
    path('users/<int:user_id>/stats/', views.UserStatsView.as_view(), name='user-stats'),
    path('users/me/stats/', views.UserStatsView.as_view(), name='my-stats'),
]