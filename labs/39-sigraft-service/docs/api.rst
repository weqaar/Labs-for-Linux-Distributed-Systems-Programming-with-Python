Python API reference
====================

The API reference is extracted from the installed package. Module imports must
not start listeners, run jobs or require a cloud subscription.

.. autosummary::

   lab_39_sigraft_service.sigraftctl.SigRaftClient
   lab_39_sigraft_service.sigraftctl.WebSocketClient
   lab_39_sigraft_service.sigraft_service.SigRaftService

.. autoclass:: lab_39_sigraft_service.sigraftctl.SigRaftClient
   :members: submit, status, metadata, graphql, close

.. autoclass:: lab_39_sigraft_service.sigraftctl.WebSocketClient
   :members: watch, close

.. autoclass:: lab_39_sigraft_service.sigraft_service.SigRaftTask

.. autoclass:: lab_39_sigraft_service.sigraft_service.SigRaftService
   :members: submit_task, get_task, transition_task, shutdown

.. autofunction:: lab_39_sigraft_service.websocket_transport.run_websocket_server

.. autofunction:: lab_39_sigraft_service.websocket_transport.load_credentials
