import type {
  Chat, Command, ConnectionStatus,
  Game, GameSnapshot, Move,
  ServerMessage, Session,
} from './types';

export function createGame(): Promise<Session> {
  return requestSession('/api/games');
}

export function joinGame(roomId: string): Promise<Session> {
  return requestSession(`/api/games/${roomId}/join`);
}

async function requestSession(url: string): Promise<Session> {
  const response = await fetch(url, {
    method : 'POST',
    signal : AbortSignal.timeout(10000),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => null);
    let message = 'Unable to join the game.';

    if (typeof error?.detail === 'string') {
      message = error.detail;
    }
    throw new Error(message);
  }

  return response.json();
}

function parseMove(uci: string): Move {
  return {
    uci,
    from      : uci.slice(0, 2),
    to        : uci.slice(2, 4),
    promotion : uci[4] ?? null,
  };
}

function readGame(snapshot: GameSnapshot): Game {
  return {
    ...snapshot,
    legal_moves : snapshot.legal_moves.map(parseMove),
    last_move   : snapshot.last_move === null ? null : parseMove(snapshot.last_move),
  };
}

interface ConnectionHandlers {
  onState  : (game: Game) => void;
  onStatus : (status: ConnectionStatus) => void;
  onError  : (message: string) => void;

  onChat?        : (chat: Chat) => void;
  onChatHistory? : (logs: Chat[]) => void;
}

export function connect(session: Session, handlers: ConnectionHandlers) {
  let socket: WebSocket;
  let stopped = false;

  let retryDelayMs = 500;
  let retryTimer      : ReturnType<typeof setTimeout>;
  let connectionTimer : ReturnType<typeof setTimeout>;

  function open() {
    handlers.onStatus('connecting');

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${location.host}/api/games/${session.id}/ws`;
    const current = new WebSocket(url);
    socket = current;

    // The connection is ready after the first authenticated state arrives. Reconnect if it times out.
    connectionTimer = setTimeout(() => current.close(), 10000);
    current.onopen = () => {
      current.send(JSON.stringify({ token: session.token }));
    };

    current.onmessage = (event) => {
      if (stopped) return;

      const message: ServerMessage = JSON.parse(event.data);
      if (message.type === 'state') {
        clearTimeout(connectionTimer);
        retryDelayMs = 500;
        handlers.onStatus('connected');
        handlers.onState(readGame(message.game));
      } else if (message.type === 'chat') {
        handlers.onChat?.({ sender: message.sender, content: message.content });
      } else if (message.type === 'chat_history') {
        handlers.onChatHistory?.(message.logs);
      } else if (message.type === 'error') {
        handlers.onError(message.message);
      }
    };

    current.onclose = (event) => {
      clearTimeout(connectionTimer);
      if (stopped) return;

      // Reconnecting cannot resolve failed authentication, expired rooms, replaced connections or policy violations.
      const cannotReconnect = [4401, 4404, 4409, 1008].includes(event.code);
      if (cannotReconnect) {
        stopped = true;
        handlers.onStatus('closed');
        handlers.onError(event.reason || 'Unable to connect. Please start a new game.');
        return;
      }

      handlers.onStatus('disconnected');

      retryTimer   = setTimeout(open, retryDelayMs);
      retryDelayMs = Math.min(retryDelayMs * 2, 5000);
    };

    current.onerror = () => current.close();
  }

  open();

  return {
    send(command: Command) {
      if (stopped || socket.readyState !== WebSocket.OPEN) return false;

      socket.send(JSON.stringify(command));
      return true;
    },

    close() {
      stopped = true;
      clearTimeout(retryTimer);
      clearTimeout(connectionTimer);
      socket.close();
    },
  };
}

