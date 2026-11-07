export interface Profile {
  id       : number;
  username : string;
  avatar   : string | null;
}

export interface SessionResponse {
  user  : Profile;
  token : string;
}


export type Color = 'white' | 'black';

export interface Seat {
  id    : string;
  color : Color;
}

export interface Entry {
  type : 'create' | 'join' | 'match' | 'cancel';
  id?  : string;
}

export interface GameSnapshot {
  revision    : number;
  fen         : string;
  turn        : Color;
  legal_moves : string[];

  moves     : string[];
  last_move : string | null;
  check     : string | null;

  outcome        : { result: string; reason: string } | null;
  draw_offer     : Color | null;
  can_claim_draw : boolean;

  players   : Partial<Record<Color, Profile>>;
  connected : Color[];
}


export type Action =
  | 'move'
  | 'resign'
  | 'claim_draw'
  | 'offer_draw'
  | 'accept_draw'
  | 'decline_draw';

export interface Command {
  type     : Action;
  revision : number;
  move?    : string;
}

export interface CommandReply {
  error: string | null;
}

export interface ClientEvents {
  enter   : (entry  : Entry,   reply: (result: CommandReply) => void) => void;
  command : (command: Command, reply: (result: CommandReply) => void) => void;
}

export interface RoomSnapshot {
  seat    : Seat | null;
  waiting : boolean;
  game    : GameSnapshot | null;
}

export interface ServerEvents {
  state          : (state  : RoomSnapshot) => void;
  session_closed : (message: string)       => void;
}

