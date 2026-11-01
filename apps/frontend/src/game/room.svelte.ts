import { io } from 'socket.io-client';

import type { Socket } from 'socket.io-client';
import type { Action, ClientEvents, CommandReply, Entry, GameSnapshot, Seat, ServerEvents } from './protocol';

type ConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'closed';
type GameSocket = Socket<ServerEvents, ClientEvents>;

export class Room {
  id   = $state(new URLSearchParams(location.search).get('game') ?? '');
  seat = $state<Seat | null>(null);
  game = $state<GameSnapshot | null>(null);

  connection = $state<ConnectionStatus>('closed');
  waiting    = $state(false);
  error      = $state('');

  private socket?: GameSocket;
  private awaitingReply = $state(false);


  validInvite  = $derived(/^[a-f0-9]{12}$/.test(this.id));
  hasOpponent  = $derived(Object.keys(this.game?.players ?? {}).length === 2);
  isMyTurn     = $derived(this.seat !== null && this.game?.turn === this.seat.color);
  inProgress   = $derived(this.hasOpponent && this.game?.outcome === null);
  hasDrawClaim = $derived(this.isMyTurn && this.game?.can_claim_draw === true);

  drawOfferedToMe = $derived(
    this.seat !== null && this.game?.draw_offer != null && this.game.draw_offer !== this.seat.color,
  );

  canSend = $derived(this.connection === 'connected' && !this.awaitingReply);

  private bothReady = $derived(this.canSend && this.inProgress && this.game?.connected.length === 2);

  allowed = $derived({
    move         : this.bothReady && this.isMyTurn,
    offer_draw   : this.bothReady && this.game?.draw_offer === null,
    accept_draw  : this.bothReady && this.drawOfferedToMe,
    decline_draw : this.bothReady && this.drawOfferedToMe,
    claim_draw   : this.bothReady && this.hasDrawClaim,
    resign       : this.canSend && this.inProgress,
  });


  open() {
    this.close();
    this.error      = '';
    this.connection = 'connecting';

    const socket: GameSocket = io({
      path                 : '/api/socket.io',
      transports           : ['websocket'],
      auth                 : { id: this.validInvite ? this.id : null },
      reconnectionDelay    : 500,
      reconnectionDelayMax : 5000,
      timeout              : 10000,
    });
    this.socket = socket;

    socket.on('seat', (seat) => {
      if (seat?.id !== this.seat?.id) this.game = null;
      this.seat    = seat ?? null;
      this.waiting = false;
      if (seat) this.setId(seat.id);
    });

    socket.on('state', (game) => {
      if (!this.seat) return;
      this.game       = game;
      this.connection = 'connected';
    });

    socket.on('waiting', (waiting) => {
      this.waiting = waiting;
      if (waiting) {
        this.seat = null;
        this.game = null;
      }
      if (!this.seat) this.setId('');
    });

    socket.on('connect', () => { this.connection = 'connected'; });
    socket.on('session_closed', (message) => {
      this.error = message;
      this.close();
    });

    const disconnected = () => {
      this.connection = socket.active ? 'disconnected' : 'closed';
      this.waiting    = false;
    };
    socket.on('disconnect', disconnected);
    socket.on('connect_error', (error) => {
      disconnected();
      if (!socket.active) this.error = error.message;
    });
  }

  private setId(id: string) {
    this.id = id;
    if (this.socket) this.socket.auth = { id: id || null };

    const url = new URL(location.href);
    url.search = id ? new URLSearchParams({ game: id }).toString() : '';
    history.replaceState(null, '', url);
  }

  close() {
    this.socket?.removeAllListeners();
    this.socket?.disconnect();

    this.socket        = undefined;
    this.connection    = 'closed';
    this.awaitingReply = false;
    this.waiting       = false;
  }

  reset() {
    this.close();
    this.seat  = null;
    this.game  = null;
    this.error = '';
  }


  enter(type: Entry['type']) {
    return this.request((socket) => socket.timeout(10000).emitWithAck('enter', {
      type, id: this.validInvite ? this.id : undefined,
    }));
  }

  send(type: Action, move?: string) {
    if (!this.game || !this.allowed[type]) return;
    const command = { type, revision: this.game.revision, move };
    return this.request((socket) => socket.timeout(10000).emitWithAck('command', command));
  }

  private async request(send: (socket: GameSocket) => Promise<CommandReply>) {
    const socket = this.socket;
    if (!socket?.connected || this.awaitingReply) return;

    this.error         = '';
    this.awaitingReply = true;
    try {
      const reply = await send(socket);
      if (this.socket === socket) this.error = reply.error ?? '';
    } catch {
      if (this.socket !== socket) return;
      this.error = 'No reply received. Reconnecting to check the current state.';

      // A request may already have run. Recover its state without resending it.
      if (socket.connected) {
        socket.disconnect().connect();
        this.connection = 'connecting';
      }
    } finally {
      if (this.socket === socket) this.awaitingReply = false;
    }
  }
}
