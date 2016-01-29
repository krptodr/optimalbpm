"""
This module holds the WebSocketBrokerClient class
"""

import threading
import time

from ws4py.client.threadedclient import WebSocketClient
from optimalbpm.broker.messaging.constants import AGENT_SHUTTING_DOWN, AGENT_RESTARTING

from of.common.messaging.websocket import BPMWebSocket
import of.common.messaging.websocket

__author__ = 'Nicklas Borjesson'

# Web socket error codes: http://tools.ietf.org/html/rfc6455#section-7.4.1


class AgentWebSocket(BPMWebSocket, WebSocketClient):
    """
    At the agent, this class holds a websocket connection to a broker.
    """

    #: Callback to a function that stops the entire agent(called when giving up reconnecting)
    stop_agent = None

    #: The thread that keeps the socket running
    run_forever_thread = None

    def __init__(self, url, protocols=None, extensions=None, heartbeat_freq=None,
                 ssl_options=None, headers=None,
                 _session_id=None, _stop_agent=None):
        """

        :param url: The remote URL
        :param protocols: List of protocols supported by this endpoint. Not used, hardcoded to ['http-only']
        :param extensions: List of extensions supported by this endpoint.
        :param heartbeat_freq: At which interval the heartbeat will be running.
        Set this to `0` or `None` to disable it entirely.
        :param ssl_options: The parameters to pass to the underlying SSLSocket, see
            https://docs.python.org/3.4/library/ssl.html#ssl.SSLSocket
        :param headers: Extra headers for the upgrade handshake request. Not used, hardcoded to contain the session id.
        :param _session_id: A session id for authentication
        :param _stop_agent: A callback for restarting the agent

        """

        protocols = ['http-only']
        headers = [("Cookie", "theme=light; session_id=" + _session_id)]
        super(AgentWebSocket, self).__init__(url, protocols=protocols,
                                                    extensions=extensions, heartbeat_freq=heartbeat_freq,
                                                    ssl_options=ssl_options, headers=headers)
        self.stop_agent = _stop_agent
        self.init(_session_id=_session_id)


    def run_forever(self):
        """
        This function spawns a thread that uses the parent classes' run_forever to continue listening
        It is overridden to provide better error handling
        """
        def run_a_socket_thread():
            try:
                super(AgentWebSocket, self).run_forever()
            except KeyboardInterrupt:
                self.close(code=1000, reason="The process is stopped.")

        self.run_forever_thread = threading.Thread(target=run_a_socket_thread, name="WebSocketBrokerClient_" +
                                                                                    self.__class__.__name__)
        self.run_forever_thread.start()

    def close(self, code=1000, reason=''):
        """
        The close function is overridden to provide better error, logging and shutdown handling.
        It is called by the websocket when the the socket has been closed for some unexpected reason.
        This in turn causes the agent to restart and try to reconnect.
        :param code: a web socket status code, see rfc6455 http://tools.ietf.org/html/rfc6455#section-7.4.1
        :param reason: the textual reason the socket was disconnected. if available.
        """
        # TODO: This should probably just try and reconnect first, and not restart the entire agent. (PROD-21)

        if code not in [AGENT_SHUTTING_DOWN, AGENT_RESTARTING]:
            print(self.log_prefix + "The peer \"" +self.address +"\" disconnected the socket(reason: " + str(reason))
        else:
            print(self.log_prefix + "Shutting down, disconnecting the peer \"" +self.address +"\"")
        # Terminate all threads
        if hasattr(self, "stream") and self.stream:
            self.terminate()
            super(AgentWebSocket, self).close(code, reason)
        else:
            # Manually handle closing
            self.closed(1006, "Going away")
            if hasattr(self, "sock") and self.sock:
                self.close_connection()
            self.environ = None

        of.common.messaging.websocket.monitor.handler.unregister_web_socket(self)

        # TODO: Handle broker restarting and/or shutting down (PROD-87)

        if code not in [AGENT_SHUTTING_DOWN, AGENT_RESTARTING]:
            # TODO: The agent should most certainly not restart, but instead try to reconnect (PROD-87)
            print(self.log_prefix + "Restarting the agent")
            self.restart_agent("Broker has terminated the connection, restarting.")

    def restart_agent(self, _reason):
        try:
            # Wait 3 seconds and then restart the agent.
            time.sleep(3)
            self.stop_agent(_reason=_reason, _restart=True)

        except Exception as e:
            print(self.log_prefix + "Error terminating self:" + str(e))


class MockupAgentWebSocketClient(AgentWebSocket):
    """
    This is a mockup class to test agent messaging and functionality.
    """
    #: Here the message is stored
    message = None
    #: Callback that is called when it gets a message
    on_message = None
    #: The behave context
    context = None
    #: The time it received the last message, for statistics
    received_last_message_at = None
    #: The time it sent the last message, for statistics
    sent_last_message_at = None

    def __init__(self, _session_id=None, _context=None):
        """
        Any changes made here must be reflected in AgentWebSocket.__init__
        Note: This does not call the super class as it doesn't want its initialization to happen
        """

        self.session_id = _session_id
        self.context = _context
        self.init(_session_id)

    def received_message(self, message):
        """
        Receives a message from a peer and puts it on the queue
        """
        self.received_last_message_at = time.perf_counter()
        super(MockupAgentWebSocketClient, self).received_message(message)

    def send_message(self, message):
        """
            Replaces the BrokerWebSocket.send_message
        """
        self.message = message
        self.sent_last_message_at = time.perf_counter()
        if self.on_message:
            self.on_message(self, self.message)
        print(self.log_prefix + "MockupWebSocket got a message to send to its peer from " + message["source"])
