% Family relationships with transitive ancestor
parent(tom, bob).
parent(bob, ann).
parent(bob, pat).
ancestor(X, Y) :- parent(X, Y).
ancestor(X, Y) :- parent(X, Z), ancestor(Z, Y).
% Query: ancestor(tom, X)
