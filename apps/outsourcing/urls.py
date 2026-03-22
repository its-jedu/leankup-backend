from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

# Register TaskViewSet without a prefix to avoid double 'tasks'
task_router = DefaultRouter()
task_router.register(r'', views.TaskViewSet, basename='task')

# Register other viewsets with their prefixes
application_router = DefaultRouter()
application_router.register(r'applications', views.ApplicationViewSet, basename='application')

notification_router = DefaultRouter()
notification_router.register(r'notifications', views.NotificationViewSet, basename='notification')

urlpatterns = [
    # Include all routers
    path('', include(task_router.urls)),
    path('', include(application_router.urls)),
    path('', include(notification_router.urls)),
    path('users/<int:user_id>/stats/', views.UserStatsView.as_view(), name='user-stats'),
    path('users/me/stats/', views.UserStatsView.as_view(), name='my-stats'),
]