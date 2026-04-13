% Simple constraint satisfaction using CLP(FD)
:- use_module(library(clpfd)).
schedule(A, B, C) :-
    [A, B, C] ins 1..3,
    all_different([A, B, C]),
    A #< B,
    label([A, B, C]).
% Query: schedule(A, B, C)
