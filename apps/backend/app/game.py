"""체스 상태와 규칙. FastAPI를 몰라도 읽고 테스트할 수 있는 코드."""

from dataclasses import dataclass, field
from typing      import Literal

import chess

Color  = Literal["white", "black"]
Action = Literal["move", "resign", "claim_draw", "offer_draw", "accept_draw", "decline_draw"]


class InvalidAction(ValueError):
    """사용자에게 설명할 명령 거절. 예상하지 못한 내부 오류와 구분한다."""


@dataclass
class Game:
    board : chess.Board = field(default_factory = chess.Board)
    moves : list[str]   = field(default_factory = list)

    revision   : int                   = 0
    draw_offer : Color | None          = None
    ending     : dict[str, str] | None = None

    @property
    def turn(self) -> Color:
        return "white" if self.board.turn == chess.WHITE else "black"

    @property
    def outcome(self) -> dict[str, str] | None:
        if self.ending:
            return self.ending

        outcome = self.board.outcome()
        if outcome:
            return {"result": outcome.result(), "reason": outcome.termination.name.lower()}

        return None

    def apply(self, color: Color, action: Action, uci: str = "") -> None:
        if self.outcome:
            raise InvalidAction("This game has already ended.")

        if action == "move":
            self._move(color, uci)

        elif action == "resign":
            result = "0-1" if color == "white" else "1-0"
            self.ending = {"result": result, "reason": "resignation"}

        elif action == "claim_draw":
            if color != self.turn or not self.board.can_claim_draw():
                raise InvalidAction("You cannot claim a draw right now.")
            self.ending = {"result": "1/2-1/2", "reason": "draw_claim"}

        elif action == "offer_draw":
            if self.draw_offer:
                raise InvalidAction("A draw offer is already pending.")
            self.draw_offer = color

        elif action in ("accept_draw", "decline_draw"):
            if not self.draw_offer or self.draw_offer == color:
                raise InvalidAction("There is no draw offer from your opponent.")
            if action == "accept_draw":
                self.ending = {"result": "1/2-1/2", "reason": "agreement"}
            self.draw_offer = None

        else:
            raise InvalidAction("Unknown action.")

        if self.outcome:
            self.draw_offer = None
        self.revision += 1

    def _move(self, color: Color, uci: str) -> None:
        if color != self.turn:
            raise InvalidAction("It is not your turn.")

        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            raise InvalidAction("Invalid move format.") from None

        if move not in self.board.legal_moves:
            raise InvalidAction("That move is not legal.")

        # SAN은 움직이기 전의 판에서 계산한다. 체크·잡기 정보가 이 판에 달려 있다.
        notation = self.board.san(move)
        self.moves.append(notation)
        self.board.push(move)

        if self.draw_offer != color:
            self.draw_offer = None  # 상대의 제안에 수를 두면 거절한 것으로 처리.

    def snapshot(self) -> dict:
        pieces = {}
        for square, piece in self.board.piece_map().items():
            square_name = chess.square_name(square)
            pieces[square_name] = piece.symbol()

        last_move = self.board.peek().uci() if self.board.move_stack else None

        king_square  = self.board.king(self.board.turn)
        checked_king = None
        if king_square is not None and self.board.is_check():
            checked_king = chess.square_name(king_square)

        outcome        = self.outcome
        legal_moves    = []
        can_claim_draw = False

        if outcome is None:
            legal_moves    = [move.uci() for move in self.board.legal_moves]
            can_claim_draw = self.board.can_claim_draw()

        return {
            "revision"    : self.revision,
            "board"       : pieces,
            "turn"        : self.turn,
            "legal_moves" : legal_moves,

            "moves"     : self.moves.copy(),
            "last_move" : last_move,
            "check"     : checked_king,

            "outcome"        : outcome,
            "draw_offer"     : self.draw_offer,
            "can_claim_draw" : can_claim_draw,
        }
