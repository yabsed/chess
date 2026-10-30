import      { http }    from '../http';
import type { Session } from './protocol';

export function createGame(): Promise<Session> {
  return http.post('games').json<Session>();
}

export function joinGame(roomId: string): Promise<Session> {
  return http.post(`games/${roomId}/join`).json<Session>();
}

