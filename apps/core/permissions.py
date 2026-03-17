from rest_framework import permissions

class IsCreatorOrReadOnly(permissions.BasePermission):
    """
    Custom permission to only allow creators of an object to edit it.
    """
    def has_object_permission(self, request, view, obj):
        # Read permissions are allowed to any request
        if request.method in permissions.SAFE_METHODS:
            return True
        
        # Write permissions are only allowed to the creator
        return obj.creator == request.user

class IsAdminOrReadOnly(permissions.BasePermission):
    """
    Custom permission to only allow admin users to edit objects.
    """
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return request.user and request.user.is_staff

class IsOwnerOrAdmin(permissions.BasePermission):
    """
    Custom permission to only allow owners or admins to access an object.
    """
    def has_object_permission(self, request, view, obj):
        # Check if user is admin
        if request.user and request.user.is_staff:
            return True
        
        # Check if user is owner (assuming obj has a 'user' or 'creator' field)
        if hasattr(obj, 'user'):
            return obj.user == request.user
        elif hasattr(obj, 'creator'):
            return obj.creator == request.user
        
        return False