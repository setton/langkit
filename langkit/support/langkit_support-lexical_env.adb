with Langkit_Support.Array_Utils;

package body Langkit_Support.Lexical_Env is

   package Entity_Arrays is new Langkit_Support.Array_Utils
     (Entity, Positive, Entity_Array);

   package Lexical_Env_Arrays is new Langkit_Support.Array_Utils
     (Lexical_Env, Positive, Lexical_Env_Vectors.Elements_Array);

   package Referenced_Envs_Arrays is new Langkit_Support.Array_Utils
     (Referenced_Env, Positive, Referenced_Envs_Vectors.Elements_Array);

   package Internal_Map_Element_Arrays is new Langkit_Support.Array_Utils
     (Internal_Map_Element, Positive, Internal_Map_Element_Array);

   procedure Pop_Rebinding
     (Rebindings        : Env_Rebindings;
      Old_Env           : Lexical_Env;
      Popped_Rebindings : out Env_Rebindings;
      New_Env           : out Lexical_Env);
   --  Look for a pair in Rebindings whose Old_Env field is "Old_Env".
   --
   --  If there is one, return its New_Env field in "New_Env", and create in
   --  "Popped_Rebindings" a set of rebindings that excludes this pair.
   --  Otherwise, forward "Old_Env" to "New_Env" and forward "Rebindings" to
   --  "Popped_Rebindings".
   --
   --  In all cases, "Popped_Rebindings" contains upon return a new ownership
   --  share.

   procedure Transitive_Reference
     (Self            : Lexical_Env;
      To_Reference    : Lexical_Env)
      with Pre => Self.Ref_Count /= No_Refcount;
   --  Reference the env To_Reference from Self, making its content accessible
   --  from Self. This is available only for ref-counted lexical environments.

   function Decorate
     (Elts       : Internal_Map_Element_Array;
      MD         : Element_Metadata;
      Rebindings : Env_Rebindings) return Entity_Array;
   --  From an array of entities, decorate every element with additional
   --  Metadata stored in MD.

   -----------------------
   -- Simple_Env_Getter --
   -----------------------

   function Simple_Env_Getter (E : Lexical_Env) return Env_Getter
   is
   begin
      Inc_Ref (E);
      return Env_Getter'(Dynamic       => False,
                         Is_Refcounted => E.Ref_Count /= No_Refcount,
                         Env           => E);
   end Simple_Env_Getter;

   --------------------
   -- Dyn_Env_Getter --
   --------------------

   function Dyn_Env_Getter
     (Fn : Getter_Fn_T; State : Getter_State_T) return Env_Getter
   is
   begin
      return Env_Getter'(True, State, Fn);
   end Dyn_Env_Getter;

   -------------
   -- Get_Env --
   -------------

   function Get_Env (Self : Env_Getter) return Lexical_Env is
   begin
      if Self.Dynamic then
         return Self.Getter_Fn (Self.Getter_State);
      else
         if Self.Env /= null then
            Inc_Ref (Self.Env);
         end if;
         return Self.Env;
      end if;
   end Get_Env;

   -------------------
   -- Is_Equivalent --
   -------------------

   function Is_Equivalent (L, R : Env_Getter) return Boolean is
   begin
      if L.Dynamic or else R.Dynamic then
         raise Constraint_Error with "trying to compare dynamic env getters";
      else
         return L.Env = R.Env;
      end if;
   end Is_Equivalent;

   -------------
   -- Inc_Ref --
   -------------

   procedure Inc_Ref (Self : Env_Getter) is
   begin
      if not Self.Dynamic and then Self.Is_Refcounted then
         Inc_Ref (Self.Env);
      end if;
   end Inc_Ref;

   -------------
   -- Dec_Ref --
   -------------

   procedure Dec_Ref (Self : in out Env_Getter) is
   begin
      if not Self.Dynamic and then Self.Is_Refcounted then
         Dec_Ref (Self.Env);
      end if;
   end Dec_Ref;

   -------------
   -- Inc_Ref --
   -------------

   procedure Inc_Ref (Self : Env_Rebinding) is
   begin
      Inc_Ref (Self.Old_Env);
      Inc_Ref (Self.New_Env);
   end Inc_Ref;

   -------------
   -- Dec_Ref --
   -------------

   procedure Dec_Ref (Self : in out Env_Rebinding) is
   begin
      Dec_Ref (Self.Old_Env);
      Dec_Ref (Self.New_Env);
   end Dec_Ref;

   -------------------
   -- Is_Equivalent --
   -------------------

   function Is_Equivalent (L, R : Env_Rebindings) return Boolean is
      function Is_Equivalent (L, R : Env_Rebinding) return Boolean is
        (Is_Equivalent (L.Old_Env, R.Old_Env)
         and then Is_Equivalent (L.New_Env, R.New_Env));
   begin
      if L = null or else R = null then
         return L = R;
      end if;

      if L.Size /= R.Size then
         return False;
      end if;

      for I in 1 .. L.Size loop
         if not Is_Equivalent (L.Rebindings (I), R.Rebindings (I)) then
            return False;
         end if;
      end loop;

      return True;
   end Is_Equivalent;

   ------------
   -- Create --
   ------------

   function Create (Bindings : Env_Rebindings_Array) return Env_Rebindings is
   begin
      if Bindings'Length = 0 then
         return null;
      end if;

      declare
         Result : constant Env_Rebindings := new Env_Rebindings_Type'
           (Size       => Bindings'Length,
            Rebindings => Bindings,
            Ref_Count  => 1);
      begin
         for R of Bindings loop
            Inc_Ref (R);
         end loop;
         return Result;
      end;
   end Create;

   -------------
   -- Inc_Ref --
   -------------

   procedure Inc_Ref (Self : Env_Rebindings) is
   begin
      if Self /= null then
         Self.Ref_Count := Self.Ref_Count + 1;
      end if;
   end Inc_Ref;

   -------------
   -- Dec_Ref --
   -------------

   procedure Dec_Ref (Self : in out Env_Rebindings) is
      procedure Unchecked_Free
      is new Ada.Unchecked_Deallocation (Env_Rebindings_Type, Env_Rebindings);
   begin
      if Self /= null then
         Self.Ref_Count := Self.Ref_Count - 1;
         if Self.Ref_Count = 0 then
            for R of Self.Rebindings loop
               Dec_Ref (R);
            end loop;
            Unchecked_Free (Self);
         end if;
      end if;
   end Dec_Ref;

   -------------
   -- Combine --
   -------------

   function Combine (L, R : Env_Rebindings) return Env_Rebindings is
      Result : Env_Rebindings;
   begin
      if L = null and then R = null then
         return null;
      elsif L = null or else L.Size = 0 then
         Inc_Ref (R);
         return R;
      elsif R = null or else R.Size = 0 then
         Inc_Ref (L);
         return L;
      end if;

      Result := new Env_Rebindings_Type (L.Size + R.Size);
      Result.Ref_Count := 1;

      for J in 1 .. L.Size loop
         Result.Rebindings (J) := L.Rebindings (J);
         Inc_Ref (Result.Rebindings (J));
      end loop;

      for J in 1 .. R.Size loop
         Result.Rebindings (J + L.Size) := R.Rebindings (J);
         Inc_Ref (Result.Rebindings (J));
      end loop;

      return Result;
   end Combine;

   ------------
   -- Append --
   ------------

   function Append
     (Self : Env_Rebindings; Binding : Env_Rebinding) return Env_Rebindings
   is
   begin
      if Binding = No_Env_Rebinding then
         Inc_Ref (Self);
         return Self;

      else
         return Create
           (if Self /= null
            then Self.Rebindings & Binding
            else (1 => Binding));
      end if;
   end Append;

   -------------
   -- Combine --
   -------------

   function Combine (L, R : Entity_Info) return Entity_Info is
   begin
      return (MD         => Combine (L.MD, R.MD),
              Rebindings => Combine (L.Rebindings, R.Rebindings));
   end Combine;

   -------------
   -- Inc_Ref --
   -------------

   procedure Inc_Ref (Self : Entity_Info) is
   begin
      Inc_Ref (Self.Rebindings);
   end Inc_Ref;

   -------------
   -- Dec_Ref --
   -------------

   procedure Dec_Ref (Self : in out Entity_Info) is
   begin
      Dec_Ref (Self.Rebindings);
   end Dec_Ref;

   ------------
   -- Create --
   ------------

   function Create (El : Element_T; MD : Element_Metadata) return Entity
   is
   begin
      return Entity'
        (El      => El,
         Info    => (MD => MD, Rebindings => null));
   end Create;

   -------------------
   -- Is_Equivalent --
   -------------------

   function Is_Equivalent (L, R : Entity) return Boolean is
   begin
      if L.El /= R.El then
         return False;
      end if;

      --  All null element are equals, regardless of their entity info
      if L.El = No_Element then
         return True;
      end if;

      --  For all other cases, make sure the entity info is equivalent
      return L.Info.MD = R.Info.MD and then Is_Equivalent (L.Info.Rebindings,
                                                            R.Info.Rebindings);
   end Is_Equivalent;

   -------------
   -- Inc_Ref --
   -------------

   procedure Inc_Ref (Self : Entity) is
   begin
      Inc_Ref (Self.Info.Rebindings);
   end Inc_Ref;

   -------------
   -- Dec_Ref --
   -------------

   procedure Dec_Ref (Self : in out Entity) is
   begin
      Dec_Ref (Self.Info.Rebindings);
   end Dec_Ref;

   ------------
   -- Create --
   ------------

   function Create
     (Parent        : Env_Getter;
      Node          : Element_T;
      Is_Refcounted : Boolean;
      Default_MD    : Element_Metadata := Empty_Metadata) return Lexical_Env
   is
   begin
      if Parent /= No_Env_Getter then
         Inc_Ref (Parent);
      end if;
      return new Lexical_Env_Type'
        (Parent                     => Parent,
         Node                       => Node,
         Referenced_Envs            => <>,
         Transitive_Referenced_Envs => <>,
         Env                        => new Internal_Envs.Map,
         Default_MD                 => Default_MD,
         Rebindings                 => null,
         Ref_Count                  => (if Is_Refcounted then 1
                                        else No_Refcount));
   end Create;

   ---------
   -- Add --
   ---------

   procedure Add
     (Self     : Lexical_Env;
      Key      : Symbol_Type;
      Value    : Element_T;
      MD       : Element_Metadata := Empty_Metadata;
      Resolver : Entity_Resolver := null)
   is
      use Internal_Envs;

      Element : constant Internal_Map_Element := (Value, MD, Resolver);
      C       : Cursor;
      Dummy   : Boolean;
   begin
      --  See Empty_Env's documentation

      if Self = Empty_Env then
         return;
      end if;

      Self.Env.Insert
        (Key, Internal_Map_Element_Vectors.Empty_Vector, C, Dummy);
      Reference (Self.Env.all, C).Element.Append (Element);
   end Add;

   ------------
   -- Remove --
   ------------

   procedure Remove
     (Self  : Lexical_Env;
      Key   : Symbol_Type;
      Value : Element_T)
   is
      V : constant Internal_Envs.Reference_Type := Self.Env.Reference (Key);
   begin
      --  Get rid of element
      for I in 1 .. V.Length loop
         if V.Get (I).Element = Value then
            V.Remove_At (I);
            exit;
         end if;
      end loop;
   end Remove;

   ---------------
   -- Reference --
   ---------------

   procedure Reference
     (Self            : Lexical_Env;
      Referenced_From : Element_T;
      Resolver        : Lexical_Env_Resolver) is
   begin
      Referenced_Envs_Vectors.Append
        (Self.Referenced_Envs, Referenced_Env'(Referenced_From, Resolver));
   end Reference;

   --------------------------
   -- Transitive_Reference --
   --------------------------

   procedure Transitive_Reference
     (Self            : Lexical_Env;
      To_Reference    : Lexical_Env) is
   begin
      Self.Transitive_Referenced_Envs.Append (To_Reference);
      Inc_Ref (To_Reference);
   end Transitive_Reference;

   ---------
   -- Get --
   ---------

   function Get
     (Self       : Lexical_Env;
      Key        : Symbol_Type;
      From       : Element_T := No_Element;
      Recursive  : Boolean := True;
      Rebindings : Env_Rebindings := null)
      return Entity_Array
   is
      Current_Rebindings : Env_Rebindings;

      use Internal_Envs;
      use Entity_Arrays;

      use Referenced_Envs_Arrays;

      function Recurse (Self : Lexical_Env) return Entity_Array is
        (Get (Self, Key, From, Recursive => False,
              Rebindings => Current_Rebindings));
      --  Recursively call Get with the same arguments except Self (set to the
      --  Self we have here) and Recursive (set to False).

      function Get_Refd_Elements
        (Self : Referenced_Env) return Entity_Array;
      --  If we can determine that From can reach Self.From_Node, return the
      --  recursive lookup of Key in Self. Otherwise, return an empty array.

      function Get_Own_Elements
        (Self       : Lexical_Env;
         Rebindings : Env_Rebindings) return Entity_Array;
      --  Return the elements for Key contained by the internal map contained
      --  in the Self environment. Decorate each element with its own metadata
      --  and with the given Rebindings.

      -----------------------
      -- Get_Refd_Elements --
      -----------------------

      function Get_Refd_Elements
        (Self : Referenced_Env) return Entity_Array
      is
         Ent : constant Entity := (Self.From_Node, No_Entity_Info);
         Env : Lexical_Env;
      begin

         --  If the client passed an origin from the request, see if the
         --  environment is reachable.

         if From /= No_Element and then not Can_Reach (Self.From_Node, From)
         then
            return Entity_Arrays.Empty_Array;
         end if;

         Env := Self.Resolver.all (Ent);

         begin
            --  Make sure that whether Recurse suceeds or raises an exception,
            --  we always Dec_Ref the returned environment so we don't leak in
            --  case of error.

            declare
               Result : constant Entity_Array := Recurse (Env);
            begin
               Dec_Ref (Env);
               return Result;
            end;

         exception
            when others =>
               Dec_Ref (Env);
               raise;
         end;
      end Get_Refd_Elements;

      ----------------------
      -- Get_Own_Elements --
      ----------------------

      function Get_Own_Elements
        (Self       : Lexical_Env;
         Rebindings : Env_Rebindings) return Entity_Array
      is
         C : Cursor := Internal_Envs.No_Element;
      begin
         if Self.Env /= null then
            C := Self.Env.Find (Key);
         end if;

         return
           (if Has_Element (C)

            --  We want to reverse the returned array, so that last inserted
            --  results are returned first.
            then Decorate
              (Internal_Map_Element_Arrays.Reverse_Array
                 (Internal_Map_Element_Vectors.To_Array (Element (C))),
               Self.Default_MD,
               Rebindings)

            else Entity_Arrays.Empty_Array);
      end Get_Own_Elements;

      function Get_Concat is new Lexical_Env_Arrays.Flat_Map_Gen
        (Entity, Entity_Array, Recurse);
      --  Return the concatenation of Recurse for all lexical envs in the input
      --  array.

      function Get_Refd_Elements is new Referenced_Envs_Arrays.Flat_Map_Gen
        (Entity, Entity_Array, Get_Refd_Elements);
      --  Likewise, but calling Get_Refd_Elements instead of Recurse

      function Can_Reach_F (El : Entity) return Boolean is
        (Can_Reach (El.El, From));

      Popped_Rebindings : Env_Rebindings;
      Own_Lookup_Env    : Lexical_Env;

   begin
      if Self = null then
         return Entity_Arrays.Empty_Array;
      end if;

      Current_Rebindings := Combine (Rebindings, Self.Rebindings);
      Pop_Rebinding
        (Current_Rebindings, Self, Popped_Rebindings, Own_Lookup_Env);

      declare
         use type Entity_Array;

         Parent_Env : constant Lexical_Env := Get_Env (Self.Parent);

         Own_Elts   : constant Entity_Array :=
            Get_Own_Elements (Own_Lookup_Env, Popped_Rebindings);
         Refd_Elts  : constant Entity_Array :=
           (if Recursive
            then Get_Refd_Elements
              (Referenced_Envs_Vectors.To_Array (Self.Referenced_Envs))
            else Entity_Arrays.Empty_Array);
         Trans_Refd_Elts : constant Entity_Array :=
            Get_Concat
              (Lexical_Env_Vectors.To_Array (Self.Transitive_Referenced_Envs));
         Parent_Elts : constant Entity_Array :=
           (if Recursive
            then Get (Parent_Env, Key, Rebindings => Popped_Rebindings)
            else Entity_Arrays.Empty_Array);

         Ret : Entity_Array :=
            Own_Elts & Refd_Elts & Trans_Refd_Elts & Parent_Elts;
         Last_That_Can_Reach : Integer := Ret'Last;
      begin
         Dec_Ref (Current_Rebindings);
         Dec_Ref (Popped_Rebindings);

         --  Only filter if a non null value was given for the From parameter

         if From /= No_Element then
            Partition (Ret, Can_Reach_F'Access, Last_That_Can_Reach);
            for I in Last_That_Can_Reach + 1 .. Ret'Last loop
               Dec_Ref (Ret (I));
            end loop;
         end if;

         return Ret (Ret'First .. Last_That_Can_Reach);
      end;
   end Get;

   ------------
   -- Orphan --
   ------------

   function Orphan (Self : Lexical_Env) return Lexical_Env is
   begin
      Inc_Ref (Self.Rebindings);
      return new Lexical_Env_Type'
        (Parent                     => No_Env_Getter,
         Node                       => Self.Node,
         Referenced_Envs            => Self.Referenced_Envs.Copy,
         Transitive_Referenced_Envs => Self.Transitive_Referenced_Envs.Copy,
         Env                        => Self.Env,
         Default_MD                 => Self.Default_MD,
         Rebindings                 => Self.Rebindings,
         Ref_Count                  => 1);
   end Orphan;

   -----------
   -- Group --
   -----------

   function Group (Envs : Lexical_Env_Array) return Lexical_Env is
      N : Lexical_Env;
   begin
      case Envs'Length is
      when 0 =>
         return Empty_Env;
      when 1 =>
         N := Envs (Envs'First);
         Inc_Ref (N);
         return N;
      when others =>
         N := new Lexical_Env_Type'
           (Parent                     => No_Env_Getter,
            Node                       => No_Element,
            Referenced_Envs            => <>,
            Transitive_Referenced_Envs => <>,
            Env                        => null,
            Default_MD                 => Empty_Metadata,
            Rebindings                 => null,
            Ref_Count                  => 1);
         for Env of Envs loop
            Transitive_Reference (N, Env);
         end loop;
         return N;
      end case;
   end Group;

   ----------------
   -- Rebind_Env --
   ----------------

   function Rebind_Env
     (Base_Env             : Lexical_Env;
      To_Rebind, Rebind_To : Env_Getter) return Lexical_Env
   is
      Base_Rebindings : constant Env_Rebindings := Base_Env.Rebindings;
      Rebinding       : constant Env_Rebinding := (To_Rebind, Rebind_To);
      Rebindings      : constant Env_Rebindings :=
         Append (Base_Rebindings, Rebinding);

      N : constant Lexical_Env :=
        new Lexical_Env_Type'
          (Parent                     => No_Env_Getter,
           Node                       => No_Element,
           Referenced_Envs            => <>,
           Transitive_Referenced_Envs => <>,
           Env                        => null,
           Default_MD                 => Empty_Metadata,
           Rebindings                 => Rebindings,
           Ref_Count                  => 1);
   begin
      Transitive_Reference (N, Base_Env);
      return N;
   end Rebind_Env;

   ----------------
   -- Rebind_Env --
   ----------------

   function Rebind_Env
     (Base_Env             : Lexical_Env;
      To_Rebind, Rebind_To : Lexical_Env) return Lexical_Env
   is
   begin
      return Rebind_Env
        (Base_Env,
         Simple_Env_Getter (To_Rebind), Simple_Env_Getter (Rebind_To));
   end Rebind_Env;

   ----------------
   -- Rebind_Env --
   ----------------

   function Rebind_Env
     (Base_Env            : Lexical_Env;
      E_Info              : Entity_Info) return Lexical_Env
   is
   begin
      --  If no info was passed, just return the original base env.
      if E_Info = No_Entity_Info then
         return Base_Env;
      end if;

      return N : constant Lexical_Env :=
        new Lexical_Env_Type'
          (Parent                     => No_Env_Getter,
           Node                       => No_Element,
           Referenced_Envs            => <>,
           Transitive_Referenced_Envs => <>,
           Env                        => null,
           Default_MD                 => Empty_Metadata,
           Rebindings                 =>
             Combine (Base_Env.Rebindings, E_Info.Rebindings),
           Ref_Count                  => 1)
      do
         Transitive_Reference (N, Base_Env);
      end return;
   end Rebind_Env;

   -------------
   -- Destroy --
   -------------

   procedure Destroy (Self : in out Lexical_Env) is
      procedure Free is
        new Ada.Unchecked_Deallocation (Lexical_Env_Type, Lexical_Env);
      Refd_Env : Lexical_Env;
   begin

      --  Do not free the internal map for ref-counted allocated environments
      --  as all maps are owned by analysis unit owned environments.

      if Self.Ref_Count = No_Refcount then
         for Elts of Self.Env.all loop
            Internal_Map_Element_Vectors.Destroy (Elts);
         end loop;
         Destroy (Self.Env);
      end if;

      --  (Transitive) referenced envs, on the other hand, are always owned by
      --  Self.

      Referenced_Envs_Vectors.Destroy (Self.Referenced_Envs);

      for Ref_Env of Self.Transitive_Referenced_Envs loop
         Refd_Env := Ref_Env;
         Dec_Ref (Refd_Env);
      end loop;
      Lexical_Env_Vectors.Destroy (Self.Transitive_Referenced_Envs);

      Dec_Ref (Self.Rebindings);

      Free (Self);
   end Destroy;

   -------------
   -- Inc_Ref --
   -------------

   procedure Inc_Ref (Self : Lexical_Env) is
   begin
      if Self.Ref_Count = No_Refcount then
         return;
      end if;

      Self.Ref_Count := Self.Ref_Count + 1;
   end Inc_Ref;

   -------------
   -- Dec_Ref --
   -------------

   procedure Dec_Ref (Self : in out Lexical_Env) is
   begin
      if Self = null or else Self.Ref_Count = No_Refcount then
         return;
      end if;

      Self.Ref_Count := Self.Ref_Count - 1;
      if Self.Ref_Count = 0 then
         Dec_Ref (Self.Parent);
         Destroy (Self);
      end if;
      Self := null;
   end Dec_Ref;

   -------------------
   -- Pop_Rebinding --
   -------------------

   procedure Pop_Rebinding
     (Rebindings        : Env_Rebindings;
      Old_Env           : Lexical_Env;
      Popped_Rebindings : out Env_Rebindings;
      New_Env           : out Lexical_Env)
   is
      Popped_Index : Natural := 0;
   begin
      --  By default, return the input

      New_Env := Old_Env;
      Popped_Rebindings := Rebindings;
      Inc_Ref (Popped_Rebindings);

      if Rebindings = null then
         return;
      end if;

      --  Look in reverse order as if there is a rebinding that we match, we
      --  want to get the last one only.

      for J in reverse 1 .. Rebindings.Size loop
         declare
            R         : Env_Rebinding renames Rebindings.Rebindings (J);
            R_Old_Env : constant Lexical_Env := Get_Env (R.Old_Env);
         begin
            if Old_Env = R_Old_Env then
               Popped_Index := J;
               New_Env := Get_Env (R.New_Env);
               exit;
            end if;
         end;
      end loop;

      if Popped_Index /= 0 then
         --  Undo the above Inc_Ref, as we will not return the same set of
         --  rebindings.
         Dec_Ref (Popped_Rebindings);

         if Rebindings.Size = 1 then
            --  We are going to remove the only rebinding Rebindings had, so we
            --  return the "null" value for rebindings.
            Popped_Rebindings := null;
            return;
         end if;

         --  Create the new rebindings set

         Popped_Rebindings := new Env_Rebindings_Type (Rebindings.Size - 1);
         Popped_Rebindings.Ref_Count := 1;

         declare
            procedure Copy (To, From : Positive);

            ----------
            -- Copy --
            ----------

            procedure Copy (To, From : Positive) is
               Dest : Env_Rebinding renames Popped_Rebindings.Rebindings (To);
            begin
               Dest := Rebindings.Rebindings (From);
               Inc_Ref (Dest);
            end Copy;

         begin
            for I in 1 .. Popped_Index - 1 loop
               Copy (I, I);
            end loop;
            for I in Popped_Index + 1 .. Rebindings.Size loop
               Copy (I - 1, I);
            end loop;
         end;
      end if;
   end Pop_Rebinding;

   --------------
   -- Decorate --
   --------------

   function Decorate
     (Elts       : Internal_Map_Element_Array;
      MD         : Element_Metadata;
      Rebindings : Env_Rebindings) return Entity_Array
   is
      function Create_Entity (Elt : Internal_Map_Element)
         return Entity;
      --  Transform an element from the environment into an entity

      function Create_Entity (Elt : Internal_Map_Element) return Entity
      is
         Resolved : Entity;
         Result   : constant Entity :=
           (El      => Elt.Element,
            Info    => (MD         => Combine (Elt.MD, MD),
                        Rebindings => Rebindings));
      begin
         if Elt.Resolver = null then
            Inc_Ref (Result.Info.Rebindings);
            return Result;
         else
            Resolved := Elt.Resolver.all (Result);
            return Resolved;
         end if;
      end Create_Entity;

      function Internal_Decorate is new Internal_Map_Element_Arrays.Map_Gen
        (Out_Type       => Entity,
         Out_Array_Type => Entity_Array,
         Transform      => Create_Entity) with Inline;
   begin
      return Internal_Decorate (Elts);
   end Decorate;

end Langkit_Support.Lexical_Env;
