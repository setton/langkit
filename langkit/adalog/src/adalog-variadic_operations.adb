------------------------------------------------------------------------------
--                               A D A L O G                                --
--                                                                          --
--                     Copyright (C) 2016, AdaCore                          --
--                                                                          --
-- This library is free software;  you can redistribute it and/or modify it --
-- under terms of the  GNU General Public License  as published by the Free --
-- Software  Foundation;  either version 3,  or (at your  option) any later --
-- version. This library is distributed in the hope that it will be useful, --
-- but WITHOUT ANY WARRANTY;  without even the implied warranty of MERCHAN- --
-- TABILITY or FITNESS FOR A PARTICULAR PURPOSE.                            --
--                                                                          --
-- As a special exception under Section 7 of GPL version 3, you are granted --
-- additional permissions described in the GCC Runtime Library Exception,   --
-- version 3.1, as published by the Free Software Foundation.               --
--                                                                          --
-- You should have received a copy of the GNU General Public License and    --
-- a copy of the GCC Runtime Library Exception along with this program;     --
-- see the files COPYING3 and COPYING.RUNTIME respectively.  If not, see    --
-- <http://www.gnu.org/licenses/>.                                          --
--                                                                          --
------------------------------------------------------------------------------

with Adalog.Operations; use Adalog.Operations;

package body Adalog.Variadic_Operations is

   ------------------
   -- Variadic_And --
   ------------------

   function Variadic_And (Rels : Relation_Array) return Relation is
      Ret : Relation;
   begin
      pragma Assert (Rels'Length > 0);

      Ret := Rels (Rels'First);

      for I in Rels'First + 1 .. Rels'Last loop
         Ret := Relation (Ret and Rels (I));
      end loop;

      return Ret;
   end Variadic_And;

   -----------------
   -- Variadic_Or --
   -----------------

   function Variadic_Or (Rels : Relation_Array) return Relation is
      Ret : Relation;
   begin
      pragma Assert (Rels'Length > 0);

      Ret := Rels (Rels'First);

      for I in Rels'First + 1 .. Rels'Last loop
         Ret := Ret or Rels (I);
      end loop;

      return Ret;
   end Variadic_Or;

end Adalog.Variadic_Operations;
