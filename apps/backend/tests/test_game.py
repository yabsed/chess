import chess
import pytest

from app.game import Color, Game, InvalidAction


def move(game: Game, uci: str) -> None:
    game.apply(game.turn, "move", uci)


def test_rejects_wrong_turn_illegal_move_and_leaving_king_in_check():
    cases: list[tuple[Color, str]] = [
        ("black", "e7e5"),
        ("white", "e2e5"),
        ("white", "bad" ),
        ("white", "0000"),
    ]

    game = Game()
    for color, uci in cases:
        with pytest.raises(InvalidAction):
            game.apply(color, "move", uci)

    assert game.revision == 0
    assert game.board.fen() == chess.STARTING_FEN

    game = Game(board = chess.Board("4r2k/8/8/8/8/8/4R3/4K3 w - - 0 1"))
    with pytest.raises(InvalidAction):
        move(game, "e2d2")


def test_checkmate_and_rejects_moves_after_end():
    game = Game()
    for uci in ["f2f3", "e7e5", "g2g4", "d8h4"]:
        move(game, uci)
    assert game.outcome == {"result": "0-1", "reason": "checkmate"}
    assert game.snapshot()["legal_moves"] == []
    assert game.moves == ["f3", "e5", "g4", "Qh4#"]

    with pytest.raises(InvalidAction):
        move(game, "a2a3")


def test_castling_both_sides():
    game = Game(board = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"))
    move(game, "e1g1")
    move(game, "e8c8")

    assert game.board.piece_at(chess.F1) == chess.Piece.from_symbol("R")
    assert game.board.piece_at(chess.D8) == chess.Piece.from_symbol("r")
    assert game.moves == ["O-O", "O-O-O"]


def test_en_passant_removes_captured_pawn():
    game = Game()
    for uci in ["e2e4", "a7a6", "e4e5", "d7d5", "e5d6"]:
        move(game, uci)

    assert game.board.piece_at(chess.D5) is None
    assert game.board.piece_at(chess.D6) == chess.Piece.from_symbol("P")


@pytest.mark.parametrize("piece", ["q", "r", "b", "n"])
def test_all_promotions_require_explicit_piece(piece):
    game = Game(board = chess.Board("7k/P7/8/8/8/8/8/7K w - - 0 1"))
    with pytest.raises(InvalidAction):
        move(game, "a7a8")

    move(game, f"a7a8{piece}")
    assert game.board.piece_at(chess.A8) == chess.Piece.from_symbol(piece.upper())


@pytest.mark.parametrize(("fen", "reason"), [
    ("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1", "stalemate"),
    ("7k/8/8/8/8/8/8/K7 w - - 0 1",    "insufficient_material"),
    ("7k/8/8/8/8/8/R7/K7 w - - 150 76", "seventyfive_moves"),
])
def test_automatic_draws(fen, reason):
    assert Game(board = chess.Board(fen)).outcome == {"result": "1/2-1/2", "reason": reason}


def test_repetition_is_claimed_not_automatically_ended():
    game = Game()
    for uci in ["g1f3", "g8f6", "f3g1", "f6g8"] * 2:
        move(game, uci)

    assert game.outcome is None
    assert game.snapshot()["can_claim_draw"]

    with pytest.raises(InvalidAction):
        game.apply("black", "claim_draw")

    game.apply("white", "claim_draw")
    assert game.outcome == {"result": "1/2-1/2", "reason": "draw_claim"}


def test_fifty_move_claim_and_fivefold_automatic_draw():
    game = Game(board = chess.Board("7k/8/8/8/8/8/R7/K7 w - - 100 51"))
    game.apply("white", "claim_draw")
    assert game.outcome == {"result": "1/2-1/2", "reason": "draw_claim"}

    game = Game()
    for uci in ["g1f3", "g8f6", "f3g1", "f6g8"] * 4:
        move(game, uci)
    assert game.outcome == {"result": "1/2-1/2", "reason": "fivefold_repetition"}


def test_draw_offer_survives_own_move_and_opponent_can_decline_or_accept():
    game = Game()
    with pytest.raises(InvalidAction):
        game.apply("white", "claim_draw")

    game.apply("white", "offer_draw")
    with pytest.raises(InvalidAction):
        game.apply("white", "accept_draw")

    move(game, "e2e4")
    assert game.draw_offer == "white"

    move(game, "e7e5")
    assert game.draw_offer is None

    game.apply("black", "offer_draw")
    game.apply("white", "decline_draw")
    assert game.draw_offer is None

    game.apply("white", "offer_draw")
    game.apply("black", "accept_draw")
    assert game.outcome == {"result": "1/2-1/2", "reason": "agreement"}


@pytest.mark.parametrize(("color", "result"), [("white", "0-1"), ("black", "1-0")])
def test_resignation(color, result):
    game = Game()
    game.apply(color, "resign")
    assert game.outcome == {"result": result, "reason": "resignation"}
