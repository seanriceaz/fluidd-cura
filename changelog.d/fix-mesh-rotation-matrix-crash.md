bump: patch
type: Fixed

Fixed a CuraEngine crash ("no value given: mesh_rotation_matrix") that made
every slice job fail, regardless of whether a rotation was applied.
