import { io } from 'socket.io-client';
import { backend, token } from '../http';

import type { Socket } from 'socket.io-client';
import type {
  Action, ClientEvents, CommandReply, Entry, GameSnapshot, Seat, ServerEvents,
} from '../protocol';

type ConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'closed';
type GameSocket = Socket<ServerEvents, ClientEvents>;

export class Room {
  linkedRoomId = $state(new URLSearchParams(location.search).get('game') ?? '');

  seat = $state<Seat | null>(null);
  game = $state<GameSnapshot | null>(null);

  connection = $state<ConnectionStatus>('closed');
  waiting    = $state(false);
  error      = $state('');

  private socket?: GameSocket;
  private awaitingReply = $state(false);


  validInvite  = $derived(/^[a-f0-9]{12}$/.test(this.linkedRoomId));
  hasOpponent  = $derived(Object.keys(this.game?.players ?? {}).length === 2);
  isMyTurn     = $derived(this.seat !== null && this.game?.turn === this.seat.color);
  inProgress   = $derived(this.hasOpponent && this.game?.outcome === null);
  hasDrawClaim = $derived(this.isMyTurn && this.game?.can_claim_draw === true);

  drawOfferedToMe = $derived(
    this.seat !== null
      && this.game?.draw_offer != null
      && this.game.draw_offer !== this.seat.color,
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

  constructor(private onclosed: () => void) {}


  open() {
    this.close();
    this.error      = '';
    this.connection = 'connecting';

    const socket: GameSocket = io(backend, {
      path                 : '/api/socket.io',
      transports           : ['websocket'],
      reconnectionDelay    : 500,
      reconnectionDelayMax : 5000,
      timeout              : 10000,

      auth: (reply) => reply({
        token,
        id: this.validInvite ? this.linkedRoomId : null,
      }),
    });
    this.socket = socket;

    socket.on('state', ({ seat, waiting, game }) => {
      const hadSeat = this.seat !== null;

      this.seat       = seat;
      this.waiting    = waiting;
      this.game       = game;

      // Commands become available only after this connection receives its state.
      this.connection = 'connected';

      if (seat)                   this.setLink(seat.id);
      else if (waiting || hadSeat) this.setLink('');
    });

    const closed = (message: string) => {
      this.close();
      this.error = message;
      this.onclosed();
    };

    const disconnected = () => {
      this.connection = socket.active ? 'disconnected' : 'closed';
      this.waiting    = false;
    };

    socket.on('session_closed', closed);
    socket.on('disconnect', (reason) => {
      if (reason === 'io server disconnect') {
        closed('Connection closed. Refresh to reconnect.');
      } else {
        disconnected();
      }
    });
    socket.on('connect_error', (error) => {
      if (socket.active) disconnected();
      else               closed(error.message);
    });
  }

  private setLink(id: string) {
    this.linkedRoomId = id;

    const url = new URL(location.href);
    if (id) url.searchParams.set('game', id);
    else    url.searchParams.delete('game');
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
    // Keep the link so another account can sign in and accept the invitation.
    this.close();
    this.seat  = null;
    this.game  = null;
    this.error = '';
  }


  enter(type: Entry['type']) {
    const entry = {
      type,
      id: this.validInvite ? this.linkedRoomId : undefined,
    };
    return this.request((socket) => socket.timeout(10000).emitWithAck('enter', entry));
  }

  send(type: Action, move?: string) {
    if (!this.game || !this.allowed[type]) return;
    const command = { type, revision: this.game.revision, move };
    return this.request((socket) => socket.timeout(10000).emitWithAck('command', command));
  }

  private async request(send: (socket: GameSocket) => Promise<CommandReply>) {
    const socket = this.socket;
    if (!socket?.connected || !this.canSend) return;

    this.error         = '';
    this.awaitingReply = true;

    try {
      const reply = await send(socket);
      if (this.socket === socket) this.error = reply.error ?? '';
    } catch {
      if (this.socket !== socket) return;

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
