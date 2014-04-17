

def patch_all():
    from filament.patcher import socket
    from filament.patcher import os
    socket._patch()
    os._patch()
