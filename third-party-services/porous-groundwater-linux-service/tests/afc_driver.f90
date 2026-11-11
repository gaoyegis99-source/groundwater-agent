! Test-only MT3D adapter: exercise the production correction on independent systems.
module mt3dms_module
  implicit none
  integer :: ncomp=1,mixelm=-1,iunittrnop(64)=0
  integer :: nodes,ncrs=1,ncol,nrow,nlay,inocross=0,l(19)
  real,allocatable :: a(:),rhs(:),cnew(:,:,:,:),rmasio(:,:,:)
  integer,allocatable :: icbund(:,:,:,:)
end module
program driver
  use mt3dms_module
  implicit none
  integer :: u,nrc
  real :: dt,time
  open(newunit=u,file='input.bin',access='stream',form='unformatted',status='old')
  read(u) ncol,nrow,nlay,dt,time
  nodes=ncol*nrow*nlay;nrc=ncol*nrow
  l=[0,-nrc,nrc,-ncol,ncol,-1,1,-ncol-nrc,-1-nrc,1-nrc,ncol-nrc, &
       -ncol+nrc,-1+nrc,1+nrc,ncol+nrc,-1-ncol,1-ncol,-1+ncol,1+ncol]
  allocate(a(nodes*19),rhs(nodes),cnew(ncol,nrow,nlay,1),icbund(ncol,nrow,nlay,1),rmasio(100,2,1))
  read(u) a,rhs,cnew,icbund
  close(u)
  call afc_apply(1,dt,time)
  open(newunit=u,file='output.bin',access='stream',form='unformatted',status='replace')
  write(u) cnew
  close(u)
end program
