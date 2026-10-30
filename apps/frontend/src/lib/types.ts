export type Color = 'white' | 'black';

export type Action =
  | 'move'       | 'resign'      | 'claim_draw'
  | 'offer_draw' | 'accept_draw' | 'decline_draw';

export type ConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'closed';

export interface Session {
  id    : string;
  token : string;
  color : Color;
}

export interface Move {
  uci       : string;
  from      : string;
  to        : string;
  promotion : string | null;
}

export interface GameSnapshot {
  revision    : number;
  board       : Record<string, string>;
  turn        : Color;
  legal_moves : string[];

  moves     : string[];
  last_move : string | null;
  check     : string | null;

  outcome        : { result: string; reason: string } | null;
  draw_offer     : Color | null;
  can_claim_draw : boolean;

  joined    : Color[];
  connected : Color[];
}

export interface Game extends Omit<GameSnapshot, 'legal_moves' | 'last_move'> {
  legal_moves : Move[];
  last_move   : Move | null;
}

export interface GameCommand {
  type     : Action;
  revision : number;
  move?    : string;
}

export interface ChatCommand {
  type    : 'chat';
  content : string;
}

export interface ChatHistoryCommand {
  type   : 'chat_history';
  limit? : number;
}

export type Command = GameCommand | ChatCommand | ChatHistoryCommand;

export interface Chat {
  sender  : string;
  content : string;
}

export type ServerMessage =
  | { type: 'state'; you: Color; game: GameSnapshot }
  | { type: 'chat'; sender: string; content: string }
  | { type: 'chat_history'; logs: Chat[] }
  | { type: 'error'; message: string };

