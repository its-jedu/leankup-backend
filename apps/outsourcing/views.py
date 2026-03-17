from django.shortcuts import render
from django.db import models  # Add this missing import

# Create your views here.
from rest_framework import viewsets, permissions, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from django.shortcuts import get_object_or_404
from .models import Task, Application
from .serializers import TaskSerializer, TaskDetailSerializer, ApplicationSerializer
from apps.core.permissions import IsCreatorOrReadOnly, IsAdminOrReadOnly

class TaskViewSet(viewsets.ModelViewSet):
    queryset = Task.objects.all()
    serializer_class = TaskSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsCreatorOrReadOnly]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['category', 'status', 'creator']
    search_fields = ['title', 'description', 'location']
    ordering_fields = ['created_at', 'budget']
    
    def get_serializer_class(self):
        if self.action == 'retrieve':
            return TaskDetailSerializer
        return TaskSerializer
    
    def get_queryset(self):
        queryset = Task.objects.all()
        # Filter by user's tasks if requested
        user_tasks = self.request.query_params.get('user_tasks', None)
        if user_tasks and self.request.user.is_authenticated:
            queryset = queryset.filter(creator=self.request.user)
        return queryset
    
    def perform_create(self, serializer):
        serializer.save(creator=self.request.user)
    
    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def apply(self, request, pk=None):
        task = self.get_object()
        
        # Check if task is open
        if task.status != 'open':
            return Response(
                {'error': 'This task is not accepting applications'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if user already applied
        if Application.objects.filter(task=task, applicant=request.user).exists():
            return Response(
                {'error': 'You have already applied to this task'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create application
        serializer = ApplicationSerializer(
            data={'task': task.id, **request.data},
            context={'request': request}
        )
        
        if serializer.is_valid():
            serializer.save(applicant=request.user, task=task)
            
            # Send notification (implement your notification logic)
            # send_application_notification.delay(task.creator.email, request.user.username, task.title)
            
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['get'])
    def applications(self, request, pk=None):
        task = self.get_object()
        
        # Only creator and admin can view applications
        if request.user != task.creator and not request.user.is_staff:
            return Response(
                {'error': 'You do not have permission to view these applications'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        applications = task.applications.all()
        serializer = ApplicationSerializer(applications, many=True, context={'request': request})
        return Response(serializer.data)

class ApplicationViewSet(viewsets.ModelViewSet):
    queryset = Application.objects.all()
    serializer_class = ApplicationSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        if user.is_staff:
            return Application.objects.all()
        # Users can see their own applications and applications to their tasks
        return Application.objects.filter(
            models.Q(applicant=user) | models.Q(task__creator=user)
        ).distinct()
    
    @action(detail=True, methods=['post'])
    def accept(self, request, pk=None):
        application = self.get_object()
        
        # Only task creator can accept
        if request.user != application.task.creator and not request.user.is_staff:
            return Response(
                {'error': 'You do not have permission to accept this application'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        application.status = 'accepted'
        application.task.status = 'in_progress'
        application.task.save()
        application.save()
        
        # Reject other applications
        application.task.applications.exclude(id=application.id).update(status='rejected')
        
        return Response({'message': 'Application accepted'})
    
    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        application = self.get_object()
        
        # Only task creator can reject
        if request.user != application.task.creator and not request.user.is_staff:
            return Response(
                {'error': 'You do not have permission to reject this application'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        application.status = 'rejected'
        application.save()
        
        return Response({'message': 'Application rejected'})