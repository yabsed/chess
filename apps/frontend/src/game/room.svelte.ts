import { io }                   from 'socket.io-client';
import { createGame, joinGame } from './api';

import type { Socket } from 'socket.io-client';
import type { Action, ClientEvents, GameSnapshot, ServerEvents, Session } from './protocol';

type ConnectionStatus =
  | 'connecting'
  | 'connected'
  | 'disconnected'
  | 'closed';

type GameSocket = Socket<ServerEvents, ClientEvents>;

export class Room {
  id   = $state(new URLSearchParams(location.search).get('game') ?? '');
  seat = $state<Session | null>(null);
  game = $state<GameSnapshot | null>(null);

  connection = $state<ConnectionStatus>('closed');
  entering   = $state(false);

  error  = $state('');
  notice = $state('');

  private socket?: GameSocket;
  private awaitingReply = $state(false);


  // Facts about the current seat and game.
  validInvite  = $derived(/^[a-f0-9]{12}$/.test(this.id));
  hasOpponent  = $derived(this.game?.joined.length === 2);
  isMyTurn     = $derived(this.seat !== null && this.game?.turn === this.seat.color);
  inProgress   = $derived(this.hasOpponent && this.game?.outcome === null);
  hasDrawClaim = $derived(this.isMyTurn && this.game?.can_claim_draw === true);

  drawOfferedToMe = $derived(
    this.seat !== null && this.game?.draw_offer != null && this.game.draw_offer !== this.seat.color,
  );

  // Resigning remains possible while the opponent is offline.
  private canSend   = $derived(this.connection === 'connected' && !this.awaitingReply);
  private bothReady = $derived(this.canSend && this.inProgress && this.game?.connected.length === 2);

  allowed = $derived({
    move         : this.bothReady && this.isMyTurn,
    offer_draw   : this.bothReady && this.game?.draw_offer === null,
    accept_draw  : this.bothReady && this.drawOfferedToMe,
    decline_draw : this.bothReady && this.drawOfferedToMe,
    claim_draw   : this.bothReady && this.hasDrawClaim,
    resign       : this.canSend && this.inProgress,
  });


  create() { return this.enter(createGame()); }
  join()   { return this.enter(joinGame(this.id)); }

  private async enter(request: Promise<Session>) {
    this.entering = true;
    this.error    = '';
    this.notice   = '';

    let seat: Session;
    try {
      seat = await request;
    } catch (cause) {
      this.error = cause instanceof Error ? cause.message : 'Unable to connect to the server.';
      return;
    } finally {
      this.entering = false;
    }

    try {
      sessionStorage.setItem(`chess:${seat.id}`, JSON.stringify(seat));
    } catch {
      this.notice = 'Browser storage is unavailable. Your seat cannot be restored after a refresh.';
    }

    this.start(seat);
  }

  restore() {
    if (!this.id) return;

    let saved;
    try {
      const stored = sessionStorage.getItem(`chess:${this.id}`);
      if (!stored) return;
      saved = JSON.parse(stored);
    } catch {
      this.error = 'Unable to read your saved session. Please join again.';
      return;
    }

    const validColor = saved?.color === 'white' || saved?.color === 'black';
    const validToken = typeof saved?.token === 'string' && saved.token.length > 0;

    if (saved?.id === this.id && validColor && validToken) this.start(saved);
  }

  private start(seat: Session) {
    this.close();

    this.id         = seat.id;
    this.seat       = seat;
    this.game       = null;
    this.error      = '';
    this.connection = 'connecting';

    const url = new URL(location.href);
    url.search = new URLSearchParams({ game: seat.id }).toString();
    history.replaceState(null, '', url);

    const socket: GameSocket = io({
      path                 : '/api/socket.io',
      transports           : ['websocket'],
      auth                 : { id: seat.id, token: seat.token },
      reconnectionDelay    : 500,
      reconnectionDelayMax : 5000,
      timeout              : 10000,
    });
    this.socket = socket;

    // Receiving a snapshot makes the room ready, including after a reconnect.
    socket.on('state', (game) => {
      this.game       = game;
      this.connection = 'connected';
    });

    socket.on('session_closed', (message) => {
      this.error = message;
      this.close();
    });

    const disconnected = () => {
      this.connection = socket.active ? 'disconnected' : 'closed';
    };

    socket.on('disconnect', disconnected);
    socket.on('connect_error', (error) => {
      disconnected();
      if (!socket.active) this.error = error.message;
    });
  }

  close() {
    // Keep the seat and snapshot so the last position and result stay visible.
    this.socket?.removeAllListeners();
    this.socket?.disconnect();

    this.socket        = undefined;
    this.connection    = 'closed';
    this.awaitingReply = false;
  }


  async send(type: Action, move?: string) {
    if (!this.game || !this.allowed[type]) return;

    // Check the socket too: commands must never enter its offline queue.
    const socket = this.socket;
    if (!socket?.connected) {
      this.error = 'Check your connection and try again.';
      return;
    }

    this.error         = '';
    this.awaitingReply = true;

    const command = { type, revision: this.game.revision, move };

    try {
      const reply = await socket.timeout(10000).emitWithAck('command', command);

      // A reply belongs to the connection that sent the command.
      if (this.socket === socket) this.error = reply.error ?? '';
    } catch {
      if (this.socket !== socket) return;
      this.error = 'No reply received. Check the position before trying again.';

      // The command may already have run. Reconnect for its state without resending it.
      if (socket.connected) {
        socket.disconnect().connect();
        this.connection = 'connecting';
      }
    } finally {
      if (this.socket === socket) this.awaitingReply = false;
    }
  }
}
